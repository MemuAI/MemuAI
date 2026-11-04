"""MemuAI as a Telegram Mini App: configuration, authentication, tunnel, listener.

Where it runs
-------------
The Mini App lives in the **MemuAI Proxy** process, next to the Telegram bot
(``app/telegram_bot.py``) and the chat engine (``app/chat.py``): the Proxy is the
process that stays in the tray while the computer is on, the bot already answers
from it, and the models are called in-process there. ``run_manager()`` is started
from the Proxy lifespan (``app/main.py``).

The desktop window (MemuAI, another process) only edits the configuration file
and reads the status file through ``/api/pg/tgapp/*`` (``app/playground_api.py``),
so both processes agree without any extra port:

* ``%APPDATA%\\MemuAI\\memuai\\tgapp.json``        — settings + the session secret;
* ``%APPDATA%\\MemuAI\\memuai\\tgapp-status.json`` — what the manager is doing now.

Isolation
---------
The tunnel (or the user's own HTTPS reverse proxy) points at a **dedicated**
listener on ``127.0.0.1:<port>`` that serves only ``/tgapp`` (``app/tgapp_api.py``).
The Proxy admin API, the OpenAI-compatible API and the desktop Playground API
are on other ports and are never reachable through the tunnel.

Every API request carries a session token issued for validated Telegram
``initData`` (HMAC-SHA256, key = HMAC_SHA256("WebAppData", bot_token)) whose
``user.id`` equals the paired Telegram user from the MemuAI preferences.

Contract for the bot (``app/telegram_bot.py``)
----------------------------------------------
``webapp_url() -> Optional[str]``   public HTTPS URL of the Mini App entry or None;
``webapp_enabled() -> bool``        the user switched the Mini App on;
``telegram_bot.on_webapp_url_changed(url)`` is called (if it exists) whenever
the public URL changes, including to ``None``.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

log = logging.getLogger("memuai.tgapp")

STATIC_DIR = Path(__file__).parent / "tgapp"
CONFIG_NAME = "tgapp.json"
STATUS_NAME = "tgapp-status.json"
ENTRY_PATH = "/tgapp/"
DEFAULT_PORT = 8641
STATUS_FRESH_SECONDS = 20.0

CLOUDFLARED_LATEST_API = "https://api.github.com/repos/cloudflare/cloudflared/releases/latest"
CLOUDFLARED_ASSET = "cloudflared-windows-amd64.exe"
CLOUDFLARED_FALLBACK_URL = ("https://github.com/cloudflare/cloudflared/releases/latest/download/"
                            + CLOUDFLARED_ASSET)
# Wave #6 (D-07): a pinned release with known sha256 digests (release
# 2026.10.7). When the GitHub API gives no digest (down, rate-limited),
# the download falls back to this pinned release instead of saving an
# unverified binary; an asset with no pinned digest fails.
CLOUDFLARED_PINNED_TAG = "2026.10.7"
CLOUDFLARED_PINNED_URL = ("https://github.com/cloudflare/cloudflared/releases/download/"
                          + CLOUDFLARED_PINNED_TAG + "/")
CLOUDFLARED_PINNED_SHA256 = {
    "cloudflared-windows-amd64.exe": "2493dbc168923bb3e59e5e7716f71deced92992eea9acd6e9d74f84ac7751343",
    "cloudflared-windows-386.exe": "88c6b972b410db0e26745f593510d772c825a13c5a2374819e3f13a05b333082",
}
CLOUDFLARED_MIN_BYTES = 8 * 1024 * 1024
CLOUDFLARED_MAX_BYTES = 200 * 1024 * 1024

TRYCLOUDFLARE_RE = re.compile(r"https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com\b", re.I)

MODES = ("cloudflare", "named", "custom")
PROTOCOLS = ("auto", "http2", "quic")
EDGE_IP_VERSIONS = ("auto", "4", "6")
NAMED_TOKEN_RE = re.compile(r"^[A-Za-z0-9+/=_-]{40,4096}$")

def _memu_dir() -> Path:
    from .config import DATA_DIR
    path = Path(DATA_DIR) / "memuai"
    path.mkdir(parents=True, exist_ok=True)
    return path

def config_path() -> Path:
    return _memu_dir() / CONFIG_NAME

def status_path() -> Path:
    return _memu_dir() / STATUS_NAME

def bin_dir() -> Path:
    path = _memu_dir() / "bin"
    path.mkdir(parents=True, exist_ok=True)
    return path

def _read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def _write_json(path: Path, value: Dict[str, Any]) -> None:
    from .storage import save_json
    save_json(str(path), value, backup=False)

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "mode": "cloudflare",
    "custom_url": "",
    "port": DEFAULT_PORT,
    "max_age_hours": 24,

    "code_control": True,

    "web_login": True,
    "secret": "",
    "secret_created": 0,

    "cf_protocol": "auto",
    "cf_edge_ip": "auto",
    "named_token": "",
    "named_url": "",
    "retry_at": 0,
    "local_fallback": False,
    "last_working_url": "",
    "last_working_at": 0,
}

def normalize_custom_url(value: Any) -> str:
    """``https://host[:port][/prefix]`` without query, fragment or credentials; '' when invalid."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        return ""
    if parts.scheme.lower() != "https" or not parts.hostname or parts.username or parts.password:
        return ""
    if parts.query or parts.fragment:
        return ""
    if any(ch in text for ch in ' "<>\\^`{|}'):
        return ""
    try:
        port = parts.port
    except ValueError:
        return ""
    netloc = parts.hostname.lower() + (f":{port}" if port else "")
    path = parts.path.rstrip("/")
    if path.endswith("/tgapp"):
        path = path[: -len("/tgapp")]
    return f"https://{netloc}{path}"

def _normalize_config(raw: Any) -> Dict[str, Any]:
    out = dict(DEFAULT_CONFIG)
    if isinstance(raw, dict):
        out.update({key: raw[key] for key in DEFAULT_CONFIG if key in raw})
    out["enabled"] = out.get("enabled") is True
    out["mode"] = out.get("mode") if out.get("mode") in MODES else "cloudflare"
    out["custom_url"] = normalize_custom_url(out.get("custom_url"))
    try:
        port = int(out.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    out["port"] = port if 1024 <= port <= 65535 else DEFAULT_PORT
    try:
        hours = int(out.get("max_age_hours") or 24)
    except (TypeError, ValueError):
        hours = 24
    # D-01: see MemuMusic config.py — cap initData age at 24h, not 7 days.
    out["max_age_hours"] = max(1, min(24, hours))
    out["code_control"] = out.get("code_control") is not False
    out["web_login"] = out.get("web_login") is not False
    out["cf_protocol"] = out.get("cf_protocol") if out.get("cf_protocol") in PROTOCOLS else "auto"
    out["cf_edge_ip"] = str(out.get("cf_edge_ip")) if str(out.get("cf_edge_ip")) in EDGE_IP_VERSIONS else "auto"
    token = str(out.get("named_token") or "").strip()
    out["named_token"] = token if NAMED_TOKEN_RE.fullmatch(token) else ""
    out["named_url"] = normalize_custom_url(out.get("named_url"))
    out["local_fallback"] = out.get("local_fallback") is True
    out["last_working_url"] = normalize_custom_url(out.get("last_working_url"))
    try:
        out["last_working_at"] = max(0, int(out.get("last_working_at") or 0))
    except (TypeError, ValueError):
        out["last_working_at"] = 0
    try:
        out["retry_at"] = float(out.get("retry_at") or 0)
    except (TypeError, ValueError):
        out["retry_at"] = 0.0
    secret = str(out.get("secret") or "")
    out["secret"] = secret if re.fullmatch(r"[0-9a-f]{64}", secret) else ""
    try:
        out["secret_created"] = int(out.get("secret_created") or 0)
    except (TypeError, ValueError):
        out["secret_created"] = 0
    return out

_CONFIG_LOCK = threading.Lock()

def load_config() -> Dict[str, Any]:
    """The settings, with a session secret created on first use."""
    with _CONFIG_LOCK:
        config = _normalize_config(_read_json(config_path()))
        if not config["secret"]:
            config["secret"] = secrets.token_hex(32)
            config["secret_created"] = int(time.time())
            try:
                _write_json(config_path(), config)
            except OSError:
                log.warning("tgapp: could not persist the session secret")
        return config

def save_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Accept only the user-editable keys; the secret is changed by ``regenerate_secret``."""
    accepted = {key: updates[key] for key in ("enabled", "mode", "custom_url", "port", "max_age_hours",
                                              "code_control", "web_login", "cf_protocol", "cf_edge_ip", "named_token",
                                              "named_url", "local_fallback")
                if isinstance(updates, dict) and key in updates}
    for key in ("custom_url", "named_url"):
        if key in accepted and str(accepted[key] or "").strip() and not normalize_custom_url(accepted[key]):
            raise ValueError("Адрес должен начинаться с https:// и не содержать параметров")
    if "named_token" in accepted:
        token = str(accepted["named_token"] or "").strip()
        if token.startswith("cloudflared"):

            token = token.split()[-1]
        if token and not NAMED_TOKEN_RE.fullmatch(token):
            raise ValueError("Это не похоже на токен Cloudflare Tunnel (длинная строка из Zero Trust → Tunnels)")
        accepted["named_token"] = token
    if isinstance(updates, dict) and updates.get("retry"):
        accepted["retry_at"] = time.time()
    if "enabled" in accepted:
        accepted["enabled"] = accepted["enabled"] is True or str(accepted["enabled"]).lower() == "true"
    if "local_fallback" in accepted:
        accepted["local_fallback"] = accepted["local_fallback"] is True or str(accepted["local_fallback"]).lower() == "true"
    current = load_config()
    with _CONFIG_LOCK:
        current.update(accepted)
        config = _normalize_config(current)
        _write_json(config_path(), config)
    return config

def regenerate_secret() -> Dict[str, Any]:
    """New secret: every issued Mini App session stops working at once."""
    current = load_config()
    with _CONFIG_LOCK:
        current["secret"] = secrets.token_hex(32)
        current["secret_created"] = int(time.time())
        config = _normalize_config(current)
        _write_json(config_path(), config)
    return config

def public_config(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = config or load_config()
    out = {key: value for key, value in config.items() if key not in ("secret", "named_token")}
    out["named_token_set"] = bool(config.get("named_token"))
    return out

def _preferences() -> Dict[str, Any]:
    try:
        from .pgdata import load_preferences
        return load_preferences()
    except Exception:
        return {}

def bot_token(prefs: Optional[Dict[str, Any]] = None) -> str:
    prefs = prefs if prefs is not None else _preferences()
    return str(prefs.get("telegram_bot_token") or "").strip()

def paired_user_id(prefs: Optional[Dict[str, Any]] = None) -> str:
    """The one Telegram user allowed in.

    A dedicated paired-user field (if the bot settings provide one) wins; the
    Chat ID of a private chat with the bot is the user's id, so the classic
    ``telegram_chat_id`` setting is the fallback."""
    prefs = prefs if prefs is not None else _preferences()
    for key in ("telegram_user_id", "telegram_paired_user_id", "telegram_paired_user", "telegram_chat_id"):
        value = prefs.get(key)
        if isinstance(value, dict):
            value = value.get("id")
        text = str(value or "").strip()
        if re.fullmatch(r"-?\d{1,20}", text):
            return text
    return ""

def _user_id_text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("id")
    text = str(value or "").strip()
    return text if re.fullmatch(r"\d{1,20}", text) else ""

def authorized_user_ids(prefs: Optional[Dict[str, Any]] = None) -> List[str]:
    """Every Telegram user allowed into the Mini App: the paired owner first, then the whitelist.

    The bot (``app.telegram_bot.authorized_user_ids``) is the authority — it knows the owner it
    paired and the ``telegram_allowed_users`` whitelist. It is imported lazily and any failure falls
    back to the settings alone, so the Mini App never depends on the bot module loading."""
    prefs = prefs if prefs is not None else _preferences()
    ids: List[str] = []
    try:
        from . import telegram_bot
        ids = [_user_id_text(x) for x in telegram_bot.authorized_user_ids(prefs) or []]
    except Exception:
        ids = []
    if not any(ids):
        ids = [_user_id_text(paired_user_id(prefs))]
        ids.extend(_user_id_text(x) for x in prefs.get("telegram_allowed_users") or [])

    for key in ("telegram_user_id", "telegram_paired_user_id", "telegram_paired_user"):
        ids.append(_user_id_text(prefs.get(key)))
    return [x for x in dict.fromkeys(ids) if x]

def is_authorized_user(user_id: Any, prefs: Optional[Dict[str, Any]] = None) -> bool:
    text = _user_id_text(user_id)
    return bool(text) and text in authorized_user_ids(prefs)

INIT_DATA_ERRORS_EN = {
    "no_token": "The bot is not set up: no token",
    "empty": "No Telegram data (initData)",
    "malformed": "Damaged Telegram data",
    "no_hash": "The Telegram data is not signed",
    "bad_hash": "The Telegram signature does not match",
    "no_date": "The Telegram data has no date",
    "future": "The Telegram data is dated in the future — check the computer clock",
    "expired": "The Telegram data has expired — open the app again",
    "no_user": "The Telegram data has no user",
}

class InitDataError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

def validate_init_data(init_data: str, token: str, *, max_age: int = 86400,
                       now: Optional[float] = None) -> Dict[str, Any]:
    """Validate ``Telegram.WebApp.initData`` exactly as core.telegram.org/bots/webapps says.

    data-check-string = every received field except ``hash``, sorted by key,
    ``key=value`` joined by ``\\n``; secret = HMAC_SHA256(key="WebAppData",
    msg=bot_token); the hex HMAC_SHA256(key=secret, msg=data-check-string) must
    equal ``hash``. ``auth_date`` must be younger than ``max_age`` seconds."""
    if not token:
        raise InitDataError("no_token", "Бот не настроен: нет токена")
    raw = str(init_data or "")
    if not raw or len(raw) > 8192:
        raise InitDataError("empty", "Нет данных Telegram (initData)")
    try:
        pairs = urllib.parse.parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise InitDataError("malformed", "Повреждённые данные Telegram") from None
    fields: Dict[str, str] = {}
    for key, value in pairs:
        if key in fields:
            raise InitDataError("malformed", "Повторяющиеся поля в данных Telegram")
        fields[key] = value
    received = fields.pop("hash", "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", received or ""):
        raise InitDataError("no_hash", "В данных Telegram нет подписи")
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received.lower()):
        raise InitDataError("bad_hash", "Подпись Telegram не сходится")
    try:
        auth_date = int(fields.get("auth_date") or 0)
    except ValueError:
        auth_date = 0
    current = time.time() if now is None else now
    if auth_date <= 0:
        raise InitDataError("no_date", "В данных Telegram нет даты")
    if auth_date > current + 300:
        raise InitDataError("future", "Дата данных Telegram в будущем — проверьте часы компьютера")
    if current - auth_date > max_age:
        raise InitDataError("expired", "Данные Telegram устарели — откройте приложение заново")
    try:
        user = json.loads(fields.get("user") or "null")
    except ValueError:
        user = None
    if not isinstance(user, dict) or not isinstance(user.get("id"), int) or isinstance(user.get("id"), bool):
        raise InitDataError("no_user", "В данных Telegram нет пользователя")
    return {"user": user, "auth_date": auth_date, "fields": fields}

def sign_init_data(fields: Dict[str, Any], token: str) -> str:
    """Build a valid initData string (tests and the local browser preview)."""
    values = {key: (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                    if isinstance(value, (dict, list)) else str(value))
              for key, value in fields.items()}
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode("utf-8"), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(values, quote_via=urllib.parse.quote)

SESSION_TTL = 12 * 3600

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))

def issue_token(user_id: str, secret: str, *, ttl: int = SESSION_TTL, scope: str = "app",
                extra: str = "", now: Optional[float] = None) -> Tuple[str, int]:
    expires = int((time.time() if now is None else now) + ttl)
    payload = json.dumps({"u": str(user_id), "e": expires, "s": scope, "x": extra,
                          "n": secrets.token_hex(6)}, separators=(",", ":")).encode()
    signature = hmac.new(bytes.fromhex(secret), b"tgapp|" + payload, hashlib.sha256).digest()
    return _b64(payload) + "." + _b64(signature), expires

def verify_token(token: str, secret: str, *, scope: str = "app",
                 now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    try:
        head, tail = str(token or "").split(".", 1)
        payload = _unb64(head)
        signature = _unb64(tail)
        expected = hmac.new(bytes.fromhex(secret), b"tgapp|" + payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(payload)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("s") != scope:
        return None
    # D-C13: keep the expiry check guarded like the MemuMusic reference
    # copy does -- a non-numeric "e" must be an invalid token, not an
    # uncaught exception (500) in the auth gate.
    try:
        if float(data.get("e") or 0) < (time.time() if now is None else now):
            return None
    except (TypeError, ValueError):
        return None
    return data

class RateLimiter:
    """Sliding-window counter per key (thread-safe, in memory)."""

    def __init__(self, limit: int, window: float) -> None:
        self.limit = int(limit)
        self.window = float(window)
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def full(self, key: str, *, now: Optional[float] = None) -> bool:
        """Whether ``key`` used up its window, without counting this check."""
        moment = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._hits.get(key)
            while bucket and moment - bucket[0] > self.window:
                bucket.popleft()
            return bool(bucket) and len(bucket) >= self.limit

    def hit(self, key: str, *, now: Optional[float] = None) -> bool:
        moment = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            while bucket and moment - bucket[0] > self.window:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(moment)
            if len(self._hits) > 2048:
                for stale in [name for name, rows in self._hits.items() if not rows][:1024]:
                    self._hits.pop(stale, None)
            return True

def parse_tunnel_url(line: str) -> Optional[str]:
    """The quick-tunnel address from a line of cloudflared output."""
    match = TRYCLOUDFLARE_RE.search(str(line or ""))
    if not match:
        return None
    url = match.group(0).lower()
    if url.startswith("https://api.trycloudflare.com"):
        return None
    return url

def _is_registered_line(line: str) -> bool:
    lowered = str(line or "").lower()
    return "registered tunnel connection" in lowered or "connection registered" in lowered

# The hosts cloudflared has to resolve before it can connect: the edge
# discovery names. When the system resolver cannot see them, the tunnel
# fetches the same addresses over DoH (or from the on-disk cache) and
# passes them to cloudflared as TUNNEL_EDGE, so a broken system DNS does
# not mean a dead tunnel.
EDGE_HOSTS = ("region1.v2.argotunnel.com", "region2.v2.argotunnel.com")
DOH_EDGE_SOURCES = (
    ("1.1.1.1", "https://1.1.1.1/dns-query"),
    ("8.8.8.8", "https://8.8.8.8/resolve"),
    ("9.9.9.9", "https://dns.quad9.net/dns-query"),
    ("dns.google", "https://dns.google/resolve"),
)
_EDGE_CACHE_NAME = "cloudflared-edge.json"
_EDGE_CACHE_TTL = 48 * 3600

# The DNS-failure message: what broke (the system resolver cannot see
# Cloudflare) and what actually helps (switch the DNS, or use your own
# tunnel) — "check the internet or VPN" was the wrong advice: the internet
# works fine, only the resolver is blind for argotunnel.com.
DNS_HINT = ("Компьютер не может найти серверы Cloudflare (DNS): не резолвится {host} — "
            "системный DNS не отдаёт адреса Cloudflare. Смените DNS на 1.1.1.1 или 8.8.8.8 "
            "либо используйте свой туннель")
DNS_HINT_GENERIC = ("Компьютер не может найти серверы Cloudflare (DNS) — системный DNS "
                    "не отдаёт адреса Cloudflare. Смените DNS на 1.1.1.1 или 8.8.8.8 "
                    "либо используйте свой туннель")

# What to do next, per failure kind (see _tunnel_error_kind): the sticky
# error panel shows one of these next to the error, so the user is never
# left with a bare "it failed". The "dns" text covers the user's own
# setup explicitly: a non-Cloudflare DNS + a VPN, which used to work.
# English twins live in TUNNEL_NEXT_STEPS_EN (by kind, not regex — the
# texts are long and the regex table in _STATUS_EN is for status lines).
TUNNEL_NEXT_STEPS = {
    "dns": ("Что делать: 1) нажмите «Повторить» — обход через DoH подхватывает край Cloudflare "
            "автоматически; 2) если раньше с этим же DNS и VPN работало, а теперь нет — отключите "
            "VPN на минуту и нажмите «Повторить» (VPN часто перехватывает DNS и не отдаёт "
            "SRV-записи argotunnel.com), затем включите VPN обратно; 3) смените DNS на 1.1.1.1 "
            "или 8.8.8.8 в настройках сети; 4) если не помогло — «Свой туннель» со своим доменом "
            "или «Свой адрес» (ngrok, VPS)."),
    "quick_api": ("Что делать: Cloudflare не выдал адрес — запрос к api.trycloudflare.com не "
                 "проходит (TLS или блокировка). Отключите на минуту VPN/антивирус и нажмите "
                 "«Повторить»; если не помогло — «Свой туннель» со своим доменом или "
                 "«Свой адрес»."),
    "tls": ("Что делать: защищённое соединение с Cloudflare не устанавливается — мешает "
           "антивирус, прокси или блокировка. Проверьте антивирус/прокси, попробуйте другую "
           "сеть или VPN; либо используйте «Свой адрес»."),
    "quic": ("Что делать: ничего — «Авто» сам переходит на HTTP/2 (TCP) после первой неудачи. "
            "Если не помогает, выберите протокол HTTP/2 вручную."),
    "local": ("Что делать: запустите MemuAI Proxy — cloudflared не может подключиться к "
             "локальному MemuAI."),
    "token": ("Что делать: скопируйте токен заново в панели Cloudflare (Zero Trust → Tunnels) "
             "и вставьте его в поле токена."),
    "config": ("Что делать: переименуйте %USERPROFILE%\\.cloudflared\\config.yaml или укажите "
              "свой адрес."),
    "timeout": ("Что делать: Cloudflare не отвечает вовремя — сервис недоступен или заблокирован. "
               "Подождите минуту и нажмите «Повторить»; попробуйте HTTP/2 или «Свой туннель»."),
    "ratelimit": ("Что делать: подождите минуту — Cloudflare временно ограничил создание быстрых "
                 "туннелей, повтор будет автоматически."),
    "unknown": ("Что делать: нажмите «Повторить» и скопируйте ошибку кнопкой ниже — пришлите её "
               "в поддержку вместе с диагностикой."),
}
TUNNEL_NEXT_STEPS_EN = {
    "dns": ("What to do: 1) press “Retry” — the DoH bypass picks up the Cloudflare edge "
           "automatically; 2) if it used to work with the same DNS and VPN and stopped — "
           "turn the VPN off for a minute and press “Retry” (a VPN often intercepts DNS and "
           "does not return the argotunnel.com SRV records), then turn the VPN back on; "
           "3) switch DNS to 1.1.1.1 or 8.8.8.8 in the network settings; 4) if that fails — "
           "“Own tunnel” with your domain or “Own URL” (ngrok, VPS)."),
    "quick_api": ("What to do: Cloudflare gave no address — the request to api.trycloudflare.com "
                 "does not go through (TLS or blocking). Turn off the VPN/antivirus for a minute "
                 "and press “Retry”; if that fails — “Own tunnel” with your domain or “Own URL”."),
    "tls": ("What to do: the secure connection to Cloudflare cannot be established — an "
           "antivirus, a proxy or blocking is in the way. Check the antivirus/proxy, try "
           "another network or VPN; or use “Own URL”."),
    "quic": ("What to do: nothing — “Auto” switches to HTTP/2 (TCP) on its own after the first "
            "failure. If that does not help, pick the HTTP/2 protocol by hand."),
    "local": ("What to do: start MemuAI Proxy — cloudflared cannot connect to the local MemuAI."),
    "token": ("What to do: copy the token again from the Cloudflare dashboard (Zero Trust → "
             "Tunnels) and paste it into the token field."),
    "config": ("What to do: rename %USERPROFILE%\\.cloudflared\\config.yaml or set your own address."),
    "timeout": ("What to do: Cloudflare does not answer in time — the service is down or blocked. "
               "Wait a minute and press “Retry”; try HTTP/2 or “Own tunnel”."),
    "ratelimit": ("What to do: wait a minute — Cloudflare temporarily limited quick tunnels, "
                 "the retry happens automatically."),
    "unknown": ("What to do: press “Retry” and copy the error with the button below — send it to "
               "support together with the diagnostics."),
}


def _tunnel_error_kind(error: str, raw: str) -> str:
    """Which part of the tunnel setup failed: 'dns' (SRV/region lookup),
    'quick_api' (api.trycloudflare.com answered nothing), 'tls', 'quic',
    'local' (127.0.0.1 refused), 'token', 'config', 'timeout', 'ratelimit'
    or 'unknown'. First match wins; the kind picks the next-step text."""
    text = f"{error or ''}\n{raw or ''}".lower()
    if "отклонил токен" in text:
        return "token"
    if "config.yaml" in text:
        return "config"
    if "не может найти серверы cloudflare (dns)" in text:
        return "dns"
    if ("argotunnel.com" in text or "trycloudflare.com" in text) and any(
            word in text for word in ("lookup", "no such host", "server misbehaving",
                                      "dns query failed", "could not lookup", "nxdomain")):
        return "dns"
    if "временно ограничил создание быстрых туннелей" in text or "too many requests" in text:
        return "ratelimit"
    if "не выдал адрес за" in text or "не подключился к cloudflare за" in text:
        return "timeout"
    if "защищённое соединение" in text:
        return "tls"
    if re.search(r"\bquic\b", text):
        return "quic"
    if "не может подключиться к" in text and ("memuai" in text or "memumusic" in text):
        return "local"
    if "не выдал адрес туннеля" in text or "failed to request quick tunnel" in text:
        return "quick_api"
    return "unknown"


def tunnel_error_report(tunnel: Any) -> Dict[str, Any]:
    """The sticky error panel payload from a tunnel, or {} when the tunnel
    has none (including a Mock in tests: a Mock's error_report() is not a
    dict, and must never leak into a snapshot). Never raises."""
    report = getattr(tunnel, "error_report", None)
    if not callable(report):
        return {}
    try:
        value = report()
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}

_DNS_FAILURE_WORDS = ("no such host", "server misbehaving", "dns query failed", "i/o timeout",
                      "could not lookup", "cannot assign requested address",
                      "network is unreachable", "no address associated with hostname",
                      "temporary failure in name resolution", "name or service not known")


def _cloudflare_dns_host(lowered: str) -> str:
    """The Cloudflare host a failure line complains about, else ''.

    Only ``argotunnel.com`` / ``trycloudflare.com`` (and the bare domains)
    count: any other host's DNS failure is not a Cloudflare problem.
    """
    # A line can mention "lookup" more than once ("Could not lookup srv
    # records on _v2-origintunneld...: lookup _v2-origintunneld..."): the
    # first word after "lookup" may be "srv", not a host. Scan them all.
    for match in re.finditer(r"lookup\s+([a-z0-9._-]+)", lowered):
        host = match.group(1).rstrip(".")
        if host in ("argotunnel.com", "trycloudflare.com", "cloudflare.com") or host.endswith(
                (".argotunnel.com", ".trycloudflare.com", ".cloudflare.com")):
            return host
    return ""


class _EdgeAddresses(str):
    """An edge-address list that remembers which resolver produced it."""

    source: str = ""

_STATUS_EN: List[Tuple["re.Pattern[str]", str]] = [(re.compile(ru), en) for ru, en in (
    (r"Cloudflare отклонил токен именованного туннеля — скопируйте его заново в Zero Trust → Tunnels",
     "Cloudflare rejected the named tunnel token — copy it again from Zero Trust → Tunnels"),
    (r"cloudflared нашёл свой config\.yaml: быстрый туннель с ним не запускается\. "
     r"Переименуйте (.+?config\.yaml) или укажите свой адрес\.",
     r"cloudflared found its own config.yaml, and a quick tunnel does not start with it. Rename \1 or set your own address."),
    (r"Cloudflare временно ограничил создание быстрых туннелей — повтор через минуту",
     "Cloudflare temporarily limited quick tunnels — retrying in a minute"),
    (r"Cloudflare не выдал адрес туннеля — нет интернета, сервис недоступен или заблокирован",
     "Cloudflare gave no tunnel address — no internet, or the service is down or blocked"),
    (r"Протокол QUIC \(UDP\) не проходит — переключаюсь на HTTP/2", "QUIC (UDP) does not get through — switching to HTTP/2"),
    (r"Компьютер не может найти серверы Cloudflare \(DNS\): не резолвится (\S+) — проверьте интернет или VPN",
     r"The computer cannot find the Cloudflare servers (DNS): \1 does not resolve — check your internet connection or VPN"),
    (r"cloudflared не может подключиться к локальному MemuAI — проверьте, что MemuAI Proxy запущен",
     "cloudflared cannot connect to the local MemuAI — check that MemuAI Proxy is running"),
    (r"Компьютер не может найти серверы Cloudflare \(DNS\) — проверьте интернет или VPN",
     "The computer cannot find Cloudflare's servers (DNS) — check the internet or VPN"),
    (r"Компьютер не может найти серверы Cloudflare \(DNS\): не резолвится (\S+) — системный DNS не отдаёт адреса Cloudflare\. Смените DNS на 1\.1\.1\.1 или 8\.8\.8\.8 либо используйте свой туннель",
     r"The computer cannot find the Cloudflare servers (DNS): \1 does not resolve — the system DNS does not return Cloudflare addresses. Switch DNS to 1.1.1.1 or 8.8.8.8, or use your own tunnel"),
    (r"Компьютер не может найти серверы Cloudflare \(DNS\) — системный DNS не отдаёт адреса Cloudflare\. Смените DNS на 1\.1\.1\.1 или 8\.8\.8\.8 либо используйте свой туннель",
     "The computer cannot find Cloudflare's servers (DNS) — the system DNS does not return Cloudflare addresses. Switch DNS to 1.1.1.1 or 8.8.8.8, or use your own tunnel"),
    (r"Не удалось установить защищённое соединение с Cloudflare — мешает антивирус, прокси или блокировка",
     "Could not open a secure connection to Cloudflare — an antivirus, a proxy or blocking is in the way"),
    (r"Нет соединения с Cloudflare — возможна блокировка; попробуйте HTTP/2, VPN или свой адрес",
     "No connection to Cloudflare — it may be blocked; try HTTP/2, a VPN or your own address"),
    (r"Автоматическая загрузка cloudflared есть только в Windows; установите cloudflared вручную",
     "cloudflared downloads automatically only on Windows; install cloudflared yourself"),
    (r"GitHub вернул HTTP (\d+)", r"GitHub answered HTTP \1"),
    (r"Неожиданный размер файла cloudflared", "Unexpected size of the cloudflared file"),
    (r"Файл cloudflared слишком большой", "The cloudflared file is too large"),
    (r"Файл cloudflared скачан не полностью", "The cloudflared file was not downloaded completely"),
    (r"Скачанный cloudflared — не программа Windows", "The downloaded cloudflared is not a Windows program"),
    (r"Контрольная сумма cloudflared не совпала — файл не сохранён", "The cloudflared checksum did not match — the file was not saved"),
    (r"cloudflared не скачан: ", "cloudflared is not downloaded: "),
    (r"cloudflared не запустился: ", "cloudflared did not start: "),
    (r"cloudflared завершился \(код (-?\d+)\)", r"cloudflared exited (code \1)"),
    (r" · (\d+) попыток подряд — нажмите «Повторить»", r" · \1 attempts in a row — press “Retry”"),
    (r"; повтор через (\d+) с", r"; retrying in \1 s"),
    (r"Cloudflare не выдал адрес за (\d+) с — сервис недоступен или заблокирован",
     r"Cloudflare gave no address in \1 s — the service is down or blocked"),
    (r"Туннель не подключился к Cloudflare за (\d+) с", r"The tunnel did not connect to Cloudflare in \1 s"),
    (r" — пробую HTTP/2", " — trying HTTP/2"),
    (r" — возможна блокировка; попробуйте VPN или свой адрес", " — it may be blocked; try a VPN or your own address"),
    (r"Адрес ещё не появился в DNS — жду", "The address is not in DNS yet — waiting"),
    (r"Локальный режим: публичная проверка отключена — работает локальный адрес",
     "Local mode: the public check is off — the local address works"),
    (r"Не открывается через интернет: ", "Does not open from the internet: "),
    (r"Адрес отвечает, но не MemuAI \(HTTP (\d+)\)", r"The address answers, but it is not MemuAI (HTTP \1)"),
    (r"Проверка через интернет не прошла \((.*)\) — адрес всё равно отправлен в Telegram",
     r"The check from the internet failed (\1) — the address was sent to Telegram anyway"),
    (r"Проверка через интернет не прошла: ", "The check from the internet failed: "),
    (r"Адрес туннеля так и не появился в DNS — запрашиваю новый", "The tunnel address never appeared in DNS — asking for a new one"),
    (r"Не удалось открыть порт (\d+)", r"Could not open port \1"),
)]

def status_en(text: str) -> str:
    """The English twin of a Russian tunnel status text (see ``_STATUS_EN``)."""
    out = str(text or "")
    for pattern, english in _STATUS_EN:
        out = pattern.sub(english, out)
    return out

def _tunnel_error_hint(line: str) -> str:
    lowered = str(line or "").lower()
    if " err " not in f" {lowered} " and "error" not in lowered and "failed" not in lowered \
            and "config.yaml" not in lowered and "429" not in lowered:
        return ""
    # Feature discovery is optional and can fail on a healthy connection.
    if "cfd-features.argotunnel.com" in lowered:
        return ""
    if "invalid tunnel secret" in lowered or "unauthorized" in lowered and "tunnel" in lowered:
        return "Cloudflare отклонил токен именованного туннеля — скопируйте его заново в Zero Trust → Tunnels"
    if "config.yaml" in lowered or "cannot determine default configuration" in lowered:
        return ("cloudflared нашёл свой config.yaml: быстрый туннель с ним не запускается. "
                "Переименуйте %USERPROFILE%\\.cloudflared\\config.yaml или укажите свой адрес.")
    if "too many requests" in lowered or "429" in lowered:
        return "Cloudflare временно ограничил создание быстрых туннелей — повтор через минуту"
    # A DNS failure for a Cloudflare host: the resolver is the problem, not
    # the internet. This runs before the "no tunnel address" branch below:
    # a failed quick-tunnel request whose cause is a failed lookup of
    # api.trycloudflare.com is a DNS failure, not "Cloudflare gave nothing".
    host = _cloudflare_dns_host(lowered)
    if host and any(part in lowered for part in _DNS_FAILURE_WORDS):
        return DNS_HINT.format(host=host)
    if "failed to request quick tunnel" in lowered or "failed to unmarshal quick tunnel" in lowered:
        return "Cloudflare не выдал адрес туннеля — нет интернета, сервис недоступен или заблокирован"
    if "quic" in lowered and ("failed to dial" in lowered or "timeout" in lowered or "no recent network activity" in lowered):
        return "Протокол QUIC (UDP) не проходит — переключаюсь на HTTP/2"
    if "127.0.0.1" in lowered or "localhost" in lowered:
        if "connection refused" in lowered or "failed to connect" in lowered:
            return ("cloudflared не может подключиться к локальному MemuAI — "
                    "проверьте, что MemuAI Proxy запущен")
    if "no such host" in lowered or "server misbehaving" in lowered:
        if "cloudflare" in lowered or "argotunnel" in lowered:
            return DNS_HINT_GENERIC
        return ""
    if "tls handshake" in lowered or "certificate" in lowered:
        return "Не удалось установить защищённое соединение с Cloudflare — мешает антивирус, прокси или блокировка"
    if "i/o timeout" in lowered or "context deadline exceeded" in lowered or "connection refused" in lowered \
            or "connection reset" in lowered or "network is unreachable" in lowered:
        return "Нет соединения с Cloudflare — возможна блокировка; попробуйте HTTP/2, VPN или свой адрес"
    return ""

async def _doh_edge_region(client, endpoint, name, record):
    """One edge host via one DoH endpoint; [] when that source does not answer."""
    import ipaddress
    import httpx

    try:
        response = await client.get(endpoint, params={"name": name, "type": record},
                                    headers={"Accept": "application/dns-json"})
        response.raise_for_status()
        answers = response.json().get("Answer", [])
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        return []
    addresses = []
    for answer in answers[:20]:
        if answer.get("type") != (1 if record == "A" else 28):
            continue
        try:
            ip = ipaddress.ip_address(answer["data"])
        except ValueError:
            continue
        if ip.is_global:
            addresses.append(f"[{ip}]:7844" if ip.version == 6 else f"{ip}:7844")
    return addresses[:3]


async def _resolve_edge_addresses(family: str, *, client=None):
    """(addresses, source): the Cloudflare edge IPs from public DoH.

    Tries 1.1.1.1, then 8.8.8.8, then Quad9: a single blocked DoH provider
    must not leave the tunnel without an edge. Returns ("", "") when no
    source answered. The returned string remembers its source in ``.source``
    (``doh:1.1.1.1`` and so on) for diagnostics.
    """
    import httpx

    record = "AAAA" if family == "6" else "A"
    own_client = client is None
    try:
        # The proxy is taken from the environment explicitly instead of
        # trusting it: httpx raises InvalidURL on some no_proxy entries
        # (e.g. "[::1]") while building a client that trusts the env, and
        # a machine whose internet requires the proxy still needs it for
        # DoH. The no_proxy list is irrelevant here: the DoH endpoints are
        # public addresses, never local ones. Verification uses the system
        # certificate store (not certifi): a machine that trusts its
        # egress proxy — or a corporate MITM CA — validates through it,
        # and a direct connection validates against the real Cloudflare
        # certificate from the same store.
        import ssl

        proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
                 or os.environ.get("ALL_PROXY") or os.environ.get("all_proxy") or None)
        client = client or httpx.AsyncClient(timeout=6.0, trust_env=False, proxy=proxy,
                                             verify=ssl.create_default_context())
        for label, endpoint in DOH_EDGE_SOURCES:
            try:
                rows = await asyncio.gather(*(
                    _doh_edge_region(client, endpoint, host, record) for host in EDGE_HOSTS))
            except Exception:
                continue
            addresses = list(dict.fromkeys(addr for batch in rows for addr in batch))[:6]
            if addresses:
                out = _EdgeAddresses(",".join(addresses))
                out.source = f"doh:{label}"
                return out, out.source
        return "", ""
    except Exception:
        # Any surprise (a broken proxy config, a bad response) means "no
        # edge from DoH" — never an exception into the tunnel loop.
        return "", ""
    finally:
        if own_client:
            with contextlib.suppress(Exception):
                await client.aclose()


async def _cloudflare_edge_addresses(family: str, *, client=None) -> str:
    """Resolve only Cloudflare Tunnel endpoints through authenticated HTTPS."""
    addresses, _source = await _resolve_edge_addresses(family, client=client)
    return addresses


def _edge_cache_path() -> Path:
    return bin_dir() / _EDGE_CACHE_NAME


def _valid_edge_addresses(value):
    """A saved edge list, or '' when it is missing, stale or malformed."""
    import ipaddress

    if not isinstance(value, str) or not value:
        return ""
    out = []
    for part in value.split(","):
        part = part.strip()
        host, sep, port = part.rpartition(":")
        if sep != ":" or port != "7844":
            return ""
        host = host.strip("[]")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return ""
        if not ip.is_global:
            return ""
        out.append(f"[{ip}]:7844" if ip.version == 6 else f"{ip}:7844")
    return ",".join(out) if out else ""


def load_edge_cache(max_age=_EDGE_CACHE_TTL):
    """(addresses, 'cache') from the last working edge lookup, else ('', '')."""
    try:
        data = json.loads(_edge_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    try:
        age = time.time() - float(data.get("at") or 0)
    except (TypeError, ValueError):
        return "", ""
    if age < 0 or age > max_age:
        return "", ""
    addresses = _valid_edge_addresses(data.get("addresses") or "")
    return (addresses, "cache") if addresses else ("", "")


def save_edge_cache(addresses, source):
    """Best effort: remember working edge addresses for the next cold start."""
    clean = _valid_edge_addresses(addresses)
    if not clean:
        return
    try:
        path = _edge_cache_path()
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"addresses": clean, "source": str(source or ""),
                                   "at": int(time.time())}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _edge_dns_failure(line: str, *, quick: bool) -> bool:
    lowered = line.lower()
    if not any(part in lowered for part in _DNS_FAILURE_WORDS):
        return False
    if re.search(r"(?<![\w.-])(?:_v2-origintunneld\._tcp|region[12]\.v2)\.argotunnel\.com\.?(?![\w.-])", lowered):
        return True
    # A named tunnel may use a regional or federal endpoint, so only the
    # global discovery names above may replace it. A quick tunnel always
    # uses the global endpoint: any Cloudflare DNS failure qualifies, and
    # that includes the SRV shutdown line and a failed lookup of
    # api.trycloudflare.com — both were invisible before.
    return bool(quick and _cloudflare_dns_host(lowered))


def _quic_trouble(line: str) -> bool:
    lowered = str(line or "").lower()
    return "quic" in lowered and ("failed" in lowered or "timeout" in lowered or "no recent network activity" in lowered)

def _expected_binary_name() -> str:
    return "cloudflared.exe" if os.name == "nt" else "cloudflared"

def find_cloudflared() -> Optional[Path]:
    """Our downloaded copy first, then one on PATH (a user who installed it)."""
    own = bin_dir() / _expected_binary_name()
    if own.is_file() and own.stat().st_size >= CLOUDFLARED_MIN_BYTES:
        return own
    found = shutil.which("cloudflared")
    return Path(found) if found else None

def _digest_from_release(release: Dict[str, Any]) -> Tuple[str, str]:
    """(download url, sha256) of the Windows binary from the GitHub release document."""
    url, digest = "", ""
    for asset in release.get("assets") or []:
        if isinstance(asset, dict) and asset.get("name") == CLOUDFLARED_ASSET:
            url = str(asset.get("browser_download_url") or "")
            value = str(asset.get("digest") or "")
            if value.lower().startswith("sha256:"):
                digest = value.split(":", 1)[1].strip().lower()
    if not digest:
        match = re.search(re.escape(CLOUDFLARED_ASSET) + r"\s*:\s*([0-9a-f]{64})", str(release.get("body") or ""), re.I)
        if match:
            digest = match.group(1).lower()
    if url and not url.startswith("https://github.com/cloudflare/cloudflared/releases/download/"):
        url = ""
    return url, digest

async def download_cloudflared(progress: Optional[Callable[[int, int], None]] = None,
                               *, client: Any = None) -> Path:
    """Download cloudflared.exe from the official GitHub release into the data folder."""
    import httpx
    if os.name != "nt" and client is None:
        raise RuntimeError("Автоматическая загрузка cloudflared есть только в Windows; установите cloudflared вручную")
    target = bin_dir() / _expected_binary_name()
    partial = target.with_suffix(target.suffix + ".part")
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15), follow_redirects=True)
    try:
        url, digest = CLOUDFLARED_FALLBACK_URL, ""
        try:
            response = await client.get(CLOUDFLARED_LATEST_API, headers={"Accept": "application/vnd.github+json"})
            if response.status_code == 200:
                found_url, digest = _digest_from_release(response.json())
                url = found_url or url
        except Exception as exc:
            log.info("tgapp: release metadata unavailable (%s)", type(exc).__name__)
        if not digest:
            # Wave #6: no checksum from the API — use the pinned release
            # instead of downloading "latest" unverified. An asset with
            # no pinned digest fails closed; nothing is saved.
            pinned = CLOUDFLARED_PINNED_SHA256.get(CLOUDFLARED_ASSET)
            if not pinned:
                raise RuntimeError("Нет контрольной суммы cloudflared для проверки — загрузка отменена. "
                                   "Установите cloudflared вручную")
            url, digest = CLOUDFLARED_PINNED_URL + CLOUDFLARED_ASSET, pinned
            log.info("tgapp: release metadata gave no digest, using pinned cloudflared %s",
                     CLOUDFLARED_PINNED_TAG)
        hasher = hashlib.sha256()
        total = 0
        async with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise RuntimeError(f"GitHub вернул HTTP {response.status_code}")
            expected = int(response.headers.get("content-length") or 0)
            if expected and not (CLOUDFLARED_MIN_BYTES <= expected <= CLOUDFLARED_MAX_BYTES):
                raise RuntimeError("Неожиданный размер файла cloudflared")
            with partial.open("wb") as handle:
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > CLOUDFLARED_MAX_BYTES:
                        raise RuntimeError("Файл cloudflared слишком большой")
                    hasher.update(chunk)
                    handle.write(chunk)
                    if progress:
                        progress(total, expected)
        if total < CLOUDFLARED_MIN_BYTES:
            raise RuntimeError("Файл cloudflared скачан не полностью")
        with partial.open("rb") as handle:
            if handle.read(2) != b"MZ" and os.name == "nt":
                raise RuntimeError("Скачанный cloudflared — не программа Windows")
        # A digest is guaranteed here (release metadata or the pin above).
        if hasher.hexdigest() != digest:
            raise RuntimeError("Контрольная сумма cloudflared не совпала — файл не сохранён")
        os.replace(partial, target)
        if os.name != "nt":
            target.chmod(0o755)
        return target
    finally:
        with contextlib.suppress(OSError):
            if partial.exists():
                partial.unlink()
        if own_client:
            await client.aclose()

class CloudflaredTunnel:
    """One ``cloudflared`` process with restart, backoff, timeouts and a retry cap.

    Quick tunnel (``tunnel --url``) or a named tunnel (``tunnel run`` with the
    token in ``TUNNEL_TOKEN``, never on the command line). ``popen`` and
    ``sleep`` are injectable for tests. Output lines are read in a thread and
    handed to the event loop; the first trycloudflare.com address wins.

    Round 21: the start can no longer hang forever — no address within
    ``url_timeout`` or no registered edge connection within
    ``register_timeout`` kills the process with a clear reason; "auto"
    protocol switches to HTTP/2 after QUIC trouble or a failed start; after
    ``max_failures`` failed starts in a row the tunnel stops retrying
    (state ``failed``) until ``retry()``.
    """

    def __init__(self, binary: Path, port: int, *, on_url: Callable[[Optional[str]], None],
                 on_state: Optional[Callable[[str, str], None]] = None,
                 popen: Callable[..., Any] = subprocess.Popen,
                 sleep: Callable[[float], Any] = asyncio.sleep,
                 protocol: str = "auto", edge_ip_version: str = "auto", token: str = "",
                 url_timeout: float = 45.0, register_timeout: float = 75.0, max_failures: int = 6,
                 auto_retry_delay: float = 300.0, max_auto_cycles: int = 3) -> None:
        self.binary = Path(binary)
        self.port = int(port)
        self.on_url = on_url
        self.on_state = on_state or (lambda state, error: None)
        self.popen = popen
        self.sleep = sleep
        self.protocol = protocol if protocol in PROTOCOLS else "auto"
        self.edge_ip_version = str(edge_ip_version) if str(edge_ip_version) in EDGE_IP_VERSIONS else "auto"
        self.token = str(token or "")
        self.url_timeout = float(url_timeout)
        self.register_timeout = float(register_timeout)
        self.max_failures = int(max_failures)
        self.use_http2 = self.protocol == "http2"
        self._edge_dns_failed = False
        self._attempt_static_edge = False
        self._edge_addresses = ""
        # Diagnostics for the UI: where the tunnel is stuck, which resolver
        # produced the edge addresses, and how many attempts are left.
        self.phase = "idle"
        self.edge_source = "system"
        self.edge_diag = ""
        self.auto_cycles = 0
        self.auto_retry_delay = float(auto_retry_delay)
        self.max_auto_cycles = int(max_auto_cycles)
        self._preflight_done = False
        self.url: Optional[str] = None
        self.registered = False
        self.process: Any = None
        self.failures = 0
        self.last_error = ""
        # The last error stays visible
        # while the tunnel keeps retrying — it stays on screen until the
        # tunnel comes online or the user dismisses it. _attempt_* is the
        # error of the attempt currently running (or just finished);
        # last_error* is the last error ever seen, with its timestamp.
        self.last_error_at: float = 0.0
        self.last_error_raw: str = ""
        self.error_history: Deque[Dict[str, Any]] = deque(maxlen=10)
        self._attempt_no: int = 0
        self._attempt_error: str = ""
        self._attempt_raw: str = ""
        self._preflight_task: Optional[asyncio.Task] = None
        self.retry_at = 0.0
        self.gave_up = False
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self._retry = asyncio.Event()
        self.lines: Deque[str] = deque(maxlen=200)

    @property
    def attempts_left(self) -> int:
        return max(0, self.max_failures - self.failures)

    @property
    def named(self) -> bool:
        return bool(self.token)

    def effective_protocol(self) -> str:
        if self.protocol == "quic":
            return "quic"
        return "http2" if self.use_http2 else ""

    def command(self) -> List[str]:
        command = [str(self.binary), "tunnel", "--no-autoupdate"]
        protocol = self.effective_protocol()
        if protocol:
            command += ["--protocol", protocol]
        if self.edge_ip_version in ("4", "6"):
            command += ["--edge-ip-version", self.edge_ip_version]
        if self.named:
            return command + ["run"]
        return command + ["--url", f"http://127.0.0.1:{self.port}"]

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.ensure_future(self._run())

    def retry(self) -> None:
        """Manual «Retry» from Settings: forget the failures and start now.

        The edge addresses stay: they were fetched over DoH or from the
        cache precisely because the system DNS cannot see Cloudflare, and
        throwing them away would repeat the same doomed attempt. The
        pre-flight runs again so a fixed system DNS is noticed.
        """
        self.failures = 0
        self.gave_up = False
        self.auto_cycles = 0
        self._preflight_done = False
        self._retry.set()
        self.start()

    @staticmethod
    def backoff(failures: int) -> float:
        return float(min(120, 2 ** max(1, min(failures, 7))))

    def _note_error(self, error: str, raw: str = "") -> None:
        """Remember the last error (and when it happened) for the sticky UI
        panel. Called from every place that learns why an attempt failed;
        never called to clear — clearing happens only on registration."""
        if not error:
            return
        self.last_error = error
        self.last_error_at = time.time()
        if raw:
            self.last_error_raw = raw[:500]

    def error_report(self) -> Dict[str, Any]:
        """Everything the sticky error panel needs (JSON-safe)."""
        kind = _tunnel_error_kind(self.last_error, self.last_error_raw)
        lines = list(self.lines)[-15:]
        if self.token:
            lines = [line.replace(self.token, "•••") for line in lines]
        return {"last_error": self.last_error,
                "last_error_en": status_en(self.last_error),
                "last_error_at": self.last_error_at,
                "last_error_raw": self.last_error_raw,
                "last_error_kind": kind,
                "next_step": TUNNEL_NEXT_STEPS.get(kind, TUNNEL_NEXT_STEPS["unknown"]),
                "next_step_en": TUNNEL_NEXT_STEPS_EN.get(kind, TUNNEL_NEXT_STEPS_EN["unknown"]),
                "error_history": [dict(entry) for entry in self.error_history],
                "recent_lines": lines}

    async def _system_resolves_edge(self) -> Optional[bool]:
        """True = the system resolver sees the edge hosts, False = it does
        not, None = the check itself was inconclusive (timeout, odd error)."""
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*(resolve_host(host) for host in EDGE_HOSTS)), timeout=5.0)
        except asyncio.TimeoutError:
            return None
        except Exception:  # noqa: BLE001 - the check must never break a start
            return None
        if any(result is True for result in results):
            return True
        if all(result is False for result in results):
            return False
        return None

    def _start_preflight(self) -> None:
        """Start the edge pre-flight WITHOUT delaying the launch.

        Waiting for the pre-flight before the first start meant that, with a VPN
        that blocks or slows DoH (1.1.1.1 / 8.8.8.8 / Quad9) the launch
        stalled silently for up to ~23 s (5 s system check + 3 x 6 s DoH)
        on every cycle — first launch, auto-cycle, manual retry — while the
        UI showed nothing new. The launch must never wait for it: the
        pre-flight runs alongside the first attempt and its addresses apply
        to the next one (the reactive branch in _run covers the first
        attempt too). The on-disk cache is instant, so it is still applied
        synchronously before the first start.
        """
        if any(os.environ.get(key) for key in ("TUNNEL_EDGE", "TUNNEL_REGION", "TUNNEL_EDGE_BIND_ADDRESS")):
            self.edge_diag = "край Cloudflare задан переменной окружения"
            return
        cached, _source = load_edge_cache()
        if cached:
            self._edge_addresses = cached
            self.edge_source = "cache"
            self.edge_diag = "край Cloudflare из кэша (DoH недоступен)"
            return
        if self._preflight_task is not None and not self._preflight_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._preflight_task = loop.create_task(self._preflight())

    async def _preflight(self) -> None:
        """When the system DNS cannot see the Cloudflare edge hosts, fetch
        them over DoH (or from the cache) so the next attempt already
        bypasses the broken resolver instead of wasting a start on it.
        When the system DNS works, nothing changes. Runs in the background
        alongside the launch (see _start_preflight)."""
        if self.phase in ("idle", "failed"):
            self.phase = "dns_check"
        if any(os.environ.get(key) for key in ("TUNNEL_EDGE", "TUNNEL_REGION", "TUNNEL_EDGE_BIND_ADDRESS")):
            self.edge_diag = "край Cloudflare задан переменной окружения"
            return
        system_ok = await self._system_resolves_edge()
        if self._stopping:
            return
        if system_ok is True:
            self.edge_source = "system"
            self.edge_diag = "системный DNS резолвит argotunnel.com"
            return
        if system_ok is False:
            self.edge_diag = "системный DNS не резолвит argotunnel.com — ищу край через DoH"
        else:
            self.edge_diag = "проверка системного DNS не дала ответа — ищу край через DoH"
        addresses, source = "", ""
        try:
            addresses, source = await _resolve_edge_addresses(self.edge_ip_version)
        except Exception:  # noqa: BLE001 - a broken DoH lookup must not kill the tunnel
            addresses, source = "", ""
        if self._stopping:
            return
        if addresses:
            self._edge_addresses = addresses
            self.edge_source = source
            self.edge_diag = f"край Cloudflare через {source} (системный DNS не резолвит argotunnel.com)"
            save_edge_cache(addresses, source)
            return
        cached, _source = load_edge_cache()
        if cached:
            self._edge_addresses = cached
            self.edge_source = "cache"
            self.edge_diag = "край Cloudflare из кэша (DoH недоступен)"
            return
        self.edge_source = "none"
        self.edge_diag = "системный DNS не резолвит argotunnel.com, DoH недоступен — попытка без обхода"

    async def _run(self) -> None:
        while not self._stopping:
            if not self._preflight_done:
                self._preflight_done = True
                # The launch never waits for the pre-flight:
                # _start_preflight applies the cache synchronously and runs
                # DoH in the background.
                self._start_preflight()
            started = time.monotonic()
            self._edge_dns_failed = False
            code = await self._run_once()
            if self._stopping:
                break
            if time.monotonic() - started > 300:
                self.failures = 0
            self.failures += 1
            if not self.registered:
                # The attempt is over: record what it failed with. The
                # sticky last_error is NOT cleared here — it stays on
                # screen while the next attempt runs.
                self.error_history.append({
                    "at": time.time(), "attempt": self._attempt_no,
                    "error": self._attempt_error or f"cloudflared завершился (код {code})",
                    "raw": self._attempt_raw})
            if (not self.registered and self._edge_dns_failed and not self._edge_addresses
                    and self.failures < self.max_failures
                    and not any(os.environ.get(key) for key in ("TUNNEL_EDGE", "TUNNEL_REGION", "TUNNEL_EDGE_BIND_ADDRESS"))):
                try:
                    # A dead DoH must not hold the retry loop in silence
                    # for ~18 s (3 sources x 6 s): 10 s total is enough to
                    # say "no edge from DoH" and move on.
                    addresses = await asyncio.wait_for(
                        _cloudflare_edge_addresses(self.edge_ip_version), timeout=10.0)
                except asyncio.TimeoutError:
                    addresses = ""
                except Exception:  # noqa: BLE001 - a broken DoH lookup must not kill the tunnel
                    addresses = ""
                if addresses:
                    self._edge_addresses = addresses
                    self.edge_source = getattr(addresses, "source", "doh") or "doh"
                    self.edge_diag = f"край Cloudflare через {self.edge_source}"
                    save_edge_cache(addresses, self.edge_source)
                else:
                    cached, _source = load_edge_cache()
                    if cached:
                        self._edge_addresses = cached
                        self.edge_source = "cache"
                        self.edge_diag = "край Cloudflare из кэша (DoH недоступен)"
            if not self.registered and not self._edge_dns_failed and self.protocol == "auto" and not self.use_http2:

                self.use_http2 = True
            if self.failures >= self.max_failures:
                self.gave_up = True
                self.retry_at = time.time() + self.auto_retry_delay
                self.phase = "failed"
                self.on_state("failed", (self._attempt_error or f"cloudflared завершился (код {code})") +
                              f" · {self.failures} попыток подряд — нажмите «Повторить»")
                self._retry.clear()
                try:
                    await asyncio.wait_for(self._retry.wait(), timeout=self.auto_retry_delay)
                except asyncio.TimeoutError:
                    pass
                self.retry_at = 0.0
                if self._stopping:
                    break
                if self._retry.is_set() or self.auto_cycles < self.max_auto_cycles:
                    # A manual «Retry», or a bounded automatic cycle:
                    # the DNS situation may have changed by now (the ISP
                    # fixed its resolver, DoH is reachable again), so the
                    # pre-flight runs again instead of waiting forever.
                    if not self._retry.is_set():
                        self.auto_cycles += 1
                    self.failures = 0
                    self.gave_up = False
                    self._preflight_done = False
                    continue
                # No more automatic cycles: wait for a manual retry.
                self._retry.clear()
                await self._retry.wait()
                continue
            delay = self.backoff(self.failures)
            self.retry_at = time.time() + delay
            self.on_state("error", (self._attempt_error or f"cloudflared завершился (код {code})") +
                          f"; повтор через {int(delay)} с")
            self._retry.clear()
            sleeper = asyncio.ensure_future(self.sleep(delay))
            waiter = asyncio.ensure_future(self._retry.wait())
            try:
                await asyncio.wait({sleeper, waiter}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (sleeper, waiter):
                    task.cancel()
            self.retry_at = 0.0

    async def _run_once(self) -> Optional[int]:
        loop = asyncio.get_running_loop()
        self._set_url(None)
        self.registered = False
        self._attempt_no += 1
        self._attempt_error = ""
        self._attempt_raw = ""
        self.phase = "starting"
        self.on_state("starting", "")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        kwargs: Dict[str, Any] = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                      creationflags=flags, cwd=str(bin_dir()))
        if self.named or self._edge_addresses:
            env = dict(os.environ)
            if self.named:
                env["TUNNEL_TOKEN"] = self.token
            if self._edge_addresses and not env.get("TUNNEL_EDGE"):
                env["TUNNEL_EDGE"] = self._edge_addresses
            kwargs["env"] = env
        self._attempt_static_edge = bool(kwargs.get("env", os.environ).get("TUNNEL_EDGE"))
        try:
            self.process = self.popen(self.command(), **kwargs)
        except (OSError, ValueError) as exc:
            self._attempt_error = f"cloudflared не запустился: {exc}"
            self._note_error(self._attempt_error)
            return None
        process = self.process
        queue: "asyncio.Queue[Optional[str]]" = asyncio.Queue()

        def pump() -> None:
            try:
                for line in iter(process.stdout.readline, ""):
                    loop.call_soon_threadsafe(queue.put_nowait, line.rstrip("\r\n"))
            except Exception:
                pass
            finally:
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=pump, name="cloudflared-output", daemon=True).start()
        started = time.monotonic()
        try:
            while True:
                timeout = None
                if not self.registered:
                    if not self.url and not self.named:
                        timeout = started + self.url_timeout - time.monotonic()
                        reason = (f"Cloudflare не выдал адрес за {int(self.url_timeout)} с — сервис недоступен "
                                  "или заблокирован")
                    else:
                        timeout = started + self.register_timeout - time.monotonic()
                        reason = (f"Туннель не подключился к Cloudflare за {int(self.register_timeout)} с"
                                  + (" — пробую HTTP/2" if self.protocol == "auto" and not self.use_http2 else
                                     " — возможна блокировка; попробуйте VPN или свой адрес"))
                    if timeout <= 0:
                        if not self._attempt_error:
                            self._attempt_error = reason
                            self._note_error(reason)
                        self._terminate()
                        break
                try:
                    line = await asyncio.wait_for(queue.get(), timeout) if timeout else await queue.get()
                except asyncio.TimeoutError:
                    if not self._attempt_error:
                        self._attempt_error = reason
                        self._note_error(reason)
                    self._terminate()
                    break
                if line is None:
                    break
                self._line(line)
        except asyncio.CancelledError:
            self._terminate()
            raise
        code = None
        with contextlib.suppress(Exception):
            code = await asyncio.to_thread(process.wait, 10)
        self._set_url(None)
        return code

    def _line(self, line: str) -> None:
        self.lines.append(line)
        dns_failure = _edge_dns_failure(line, quick=not self.named)
        if self._attempt_static_edge and "edge discovery" in line.lower() and dns_failure:
            # cloudflared also checks system DNS for diagnostics with a static edge list.
            return
        if not self.registered and dns_failure:
            self._edge_dns_failed = True
        hint = _tunnel_error_hint(line)
        if hint and not self.registered:
            self._attempt_error = hint
            self._attempt_raw = line
            self._note_error(hint, line)
        if not dns_failure and _quic_trouble(line) and self.protocol == "auto":
            self.use_http2 = True
        url = None if self.named else parse_tunnel_url(line)
        if url and not self.url:
            self.url = url
            self.phase = "connecting"
            self.on_state("connecting", "")
        if (self.url or self.named) and not self.registered and _is_registered_line(line):
            self.registered = True
            self.last_error = ""
            self.last_error_at = 0.0
            self.last_error_raw = ""
            self._attempt_error = ""
            self._attempt_raw = ""
            self.failures = 0
            self.phase = "online"
            if self.url:
                self._set_url(self.url, force=True)
            self.on_state("online", "")

    def _set_url(self, url: Optional[str], *, force: bool = False) -> None:
        if url is None:
            changed = self.url is not None or force
            self.url = None
            if changed:
                self.on_url(None)
        else:
            self.on_url(url)

    def _terminate(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        with contextlib.suppress(Exception):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(5)
                except Exception:
                    process.kill()

    async def stop(self) -> None:
        self._stopping = True
        self._retry.set()
        self._terminate()
        preflight, self._preflight_task = self._preflight_task, None
        if preflight is not None and not preflight.done():
            preflight.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await preflight
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if self.url is not None:
            self.url = None
            self.on_url(None)

DOH_ENDPOINTS = ("https://1.1.1.1/dns-query", "https://8.8.8.8/resolve")
VERIFY_TIMEOUT = 150.0
VERIFY_EVERY = 4.0
RESOLVE_TIMEOUT = 10.0
_NONCE = secrets.token_hex(8)

def instance_nonce() -> str:
    """Random per process: /tgapp/healthz echoes it, so a check over the
    public URL proves the answer came from *this* listener."""
    return _NONCE

def remember_working_url(url: str) -> None:
    """Cache the last public address that passed DNS + health check.

    Best effort only: a failed write must never break publishing. The
    secret and the named-tunnel token are preserved exactly as
    ``save_config`` preserves them (``_normalize_config`` keeps a valid
    secret, and the write goes through the same lock)."""
    base = normalize_custom_url(url)
    if not base:
        return
    try:
        config = load_config()
        with _CONFIG_LOCK:
            config["last_working_url"] = base
            config["last_working_at"] = int(time.time())
            config = _normalize_config(config)
            _write_json(config_path(), config)
    except OSError:
        log.warning("tgapp: could not cache the last working address")

async def resolve_host(host: str) -> Optional[bool]:
    """The system resolver decides: True = resolves, False = NXDOMAIN / no
    data, None = the resolver itself failed (timeout, temporary failure) and
    nothing can be concluded. External DoH is never consulted here: a host
    that the system cannot resolve is not assumed to be missing, and a host
    the system does resolve is not held back by an unreachable DoH service."""
    try:
        await asyncio.wait_for(
            asyncio.to_thread(socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM),
            timeout=RESOLVE_TIMEOUT)
    except asyncio.TimeoutError:
        return None
    except socket.gaierror as exc:
        if exc.errno in (getattr(socket, "EAI_NONAME", -2), getattr(socket, "EAI_NODATA", -5)):
            return False
        return None
    except OSError:
        return None
    return True

async def doh_resolve(host: str, client: Any) -> Optional[bool]:
    """Does ``host`` resolve in public DNS? True/False, None when no DoH
    resolver could be reached (blocked network). Only a tiebreaker for the
    case the system resolver was inconclusive — never a gate on its own."""
    for endpoint in DOH_ENDPOINTS:
        for record in ("A", "AAAA"):
            try:
                response = await client.get(endpoint, params={"name": host, "type": record},
                                            headers={"accept": "application/dns-json"}, timeout=6)
                if response.status_code != 200:
                    continue
                data = response.json()
            except Exception:
                continue
            try:
                status = int(data.get("Status", 2))
            except (TypeError, ValueError):
                continue
            if status == 0:
                try:
                    has_answer = any(int(row.get("type", 0)) in (1, 28)
                                     for row in data.get("Answer") or [])
                except (TypeError, ValueError):
                    has_answer = False
                if has_answer:
                    return True
                # NOERROR without records: the name may simply not be
                # published yet — inconclusive, keep asking other endpoints.
                continue
            if status == 3:
                # NXDOMAIN is the only conclusive negative answer.
                return False
            # SERVFAIL / REFUSED / FORMERR and friends say the DNS server
            # itself failed — never proof the host does not exist.
            continue
    return None

async def check_public_url(base: str, client: Any) -> Tuple[str, str]:
    """One reachability probe of the public address.

    Returns ``("ok"|"dns"|"unreachable"|"http", detail)``: ``"dns"`` only
    when the system resolver definitively says the host is not in DNS;
    ``"unreachable"`` when nothing could be concluded (the resolver failed
    and DoH was unreachable too, or the health check itself failed) — that
    must never be treated as a DNS failure, or a blocked DoH service would
    keep killing a working tunnel forever."""
    host = urllib.parse.urlsplit(base).hostname or ""
    resolved = await resolve_host(host)
    if resolved is None:
        resolved = await doh_resolve(host, client)
    if resolved is False:
        return "dns", "Адрес ещё не появился в DNS — жду"
    try:
        response = await client.get(base.rstrip("/") + "/tgapp/healthz", timeout=10)
        data = response.json() if response.status_code == 200 else {}
    except Exception as exc:
        kind = "unreachable" if resolved is None else "http"
        return kind, f"Не открывается через интернет: {type(exc).__name__} (хост: {host})"
    if data.get("app") == "memuai-tgapp" and data.get("nonce") == instance_nonce():
        return "ok", ""
    return "http", f"Адрес отвечает, но не MemuAI (HTTP {response.status_code})"

def _port_free(port: int) -> bool:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        try:
            probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False

def pick_port(preferred: int) -> int:
    for port in range(preferred, min(65535, preferred + 40) + 1):
        if _port_free(port):
            return port
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])

def _make_server(app: Any, port: int) -> Any:
    import uvicorn

    class _QuietServer(uvicorn.Server):
        """Runs inside the Proxy event loop: never touch the process signal handlers."""

        @contextlib.contextmanager
        def capture_signals(self):  # type: ignore[override]
            yield

        def install_signal_handlers(self) -> None:
            return None

        async def serve(self, *args: Any, **kwargs: Any) -> None:
            try:
                await super().serve(*args, **kwargs)
            except SystemExit:
                log.warning("tgapp: the Mini App listener could not open port %s", port)

    return _QuietServer(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None,
                                       access_log=False, lifespan="off",
                                       timeout_keep_alive=20, ws="none"))

class Manager:
    """Keeps the listener and the tunnel in the state the settings ask for."""

    def __init__(self) -> None:
        self.state = "off"
        self.error = ""
        self.url: Optional[str] = None
        self.port = 0
        self.mode = "cloudflare"
        self.download = {"done": 0, "total": 0}
        self.server: Any = None
        self.server_task: Optional[asyncio.Task] = None
        self.tunnel: Optional[CloudflaredTunnel] = None
        self._download_task: Optional[asyncio.Task] = None
        self._download_retry_at = 0.0
        self._preferred_port = 0
        self._signature = ""
        # Wave #8 (D-13): a busy port used to be retried every 2 s forever
        # (reconcile runs on a 2 s loop and _ensure_server never gave up).
        # Now each failed bind schedules the next attempt with backoff
        # (5, 10, 20, 40, 80, 120 s); a settings change, wake() or the
        # manual «Retry» (retry_at) resets it, so recovery stays fast.
        self._server_failures = 0
        self._server_retry_at = 0.0
        self._notified: Any = object()
        self._wake = asyncio.Event()
        self.detail = ""
        self.retry_at = 0.0
        self.verified = False
        self._verify_task: Optional[asyncio.Task] = None
        self._retry_handled: Optional[float] = None
        self.local_fallback = False
        self.last_working_url = ""
        self._last_check: Dict[str, Any] = {}
        # A live tunnel can die quietly (the edge dropped it, the ISP
        # started blocking it): probe the public healthz every minute and
        # restart the tunnel after 3 consecutive "unreachable" results.
        # DNS and HTTP failures never restart it — the address may simply
        # not have propagated yet, or the local server is the problem.
        self._health_failures = 0
        self._health_checked_at = float("-inf")
        self._health_restarts = 0
        self._health_last_restart = float("-inf")

        self.probe: Optional[Callable[[str], Any]] = None

    def _cached_working_url(self) -> str:
        if self.last_working_url:
            return self.last_working_url
        try:
            return str(_normalize_config(_read_json(config_path())).get("last_working_url") or "")
        except Exception:
            return ""

    def _diagnostics(self, local_url: str) -> Dict[str, Any]:
        check = self._last_check or {}
        host = check.get("host") or (urllib.parse.urlsplit(self.url or "").hostname or "")
        tunnel = self.tunnel
        report = tunnel_error_report(tunnel)
        return {"host": host, "error": check.get("detail") or self.detail or self.error,
                "kind": check.get("kind") or "",
                "resolver": (tunnel.edge_source if tunnel is not None else "system"),
                "checked_at": check.get("checked_at") or 0, "local_url": local_url,
                "last_working_url": self._cached_working_url(),
                "local_fallback": self.local_fallback,
                "phase": (tunnel.phase if tunnel is not None else ""),
                "edge_source": (tunnel.edge_source if tunnel is not None else ""),
                "attempts_left": (tunnel.attempts_left if tunnel is not None else 0),
                "dns_detail": (tunnel.edge_diag if tunnel is not None else ""),
                "last_error": report.get("last_error", ""),
                "last_error_en": report.get("last_error_en", ""),
                "last_error_at": report.get("last_error_at", 0.0),
                "last_error_raw": report.get("last_error_raw", ""),
                "last_error_kind": report.get("last_error_kind", ""),
                "next_step": report.get("next_step", ""),
                "next_step_en": report.get("next_step_en", ""),
                "error_history": report.get("error_history", []),
                "recent_lines": report.get("recent_lines", [])}

    def snapshot(self) -> Dict[str, Any]:
        tunnel = self.tunnel
        report = tunnel_error_report(tunnel)
        local_url = f"http://127.0.0.1:{self.port or DEFAULT_PORT}{ENTRY_PATH}"
        return {"pid": os.getpid(), "heartbeat": time.time(), "state": self.state, "error": self.error,
                "error_en": status_en(self.error), "detail_en": status_en(self.detail),
                "url": self.public_url(), "base": self.url or "", "port": self.port, "mode": self.mode,
                "download": dict(self.download), "detail": self.detail, "verified": self.verified,
                "retry_at": (tunnel.retry_at if tunnel is not None else 0.0) or self.retry_at,
                "protocol": (tunnel.effective_protocol() or "auto") if tunnel is not None else "",
                "failures": tunnel.failures if tunnel is not None else 0,
                "phase": (tunnel.phase if tunnel is not None else ""),
                "edge_source": (tunnel.edge_source if tunnel is not None else ""),
                "attempts_left": (tunnel.attempts_left if tunnel is not None else 0),
                "dns_detail": (tunnel.edge_diag if tunnel is not None else ""),
                "last_error": report.get("last_error", ""),
                "last_error_en": report.get("last_error_en", ""),
                "last_error_at": report.get("last_error_at", 0.0),
                "last_error_raw": report.get("last_error_raw", ""),
                "last_error_kind": report.get("last_error_kind", ""),
                "next_step": report.get("next_step", ""),
                "next_step_en": report.get("next_step_en", ""),
                "error_history": report.get("error_history", []),
                "recent_lines": report.get("recent_lines", []),
                "local_url": local_url, "last_working_url": self._cached_working_url(),
                "diagnostics": self._diagnostics(local_url)}

    def public_url(self) -> Optional[str]:
        if self.state != "online" or not self.url:
            return None
        return self.url.rstrip("/") + ENTRY_PATH

    def write_status(self) -> None:
        try:
            _write_json(status_path(), self.snapshot())
        except OSError:
            pass

    def _set_state(self, state: str, error: str = "") -> None:
        self.state, self.error = state, error
        self.write_status()
        self._notify()

    def _notify(self) -> None:
        url = self.public_url()
        if url == self._notified:
            return
        self._notified = url
        notify_bot(url)

    def desired(self) -> Dict[str, Any]:
        config = load_config()
        prefs = _preferences()
        reasons = []
        if not config["enabled"]:
            reasons.append("disabled")
        if not bot_token(prefs):
            reasons.append("no_token")
        if not authorized_user_ids(prefs):
            reasons.append("no_user")
        if not (STATIC_DIR / "index.html").is_file():
            reasons.append("no_static")
        if config["mode"] == "custom" and not config["custom_url"]:
            reasons.append("no_custom_url")
        if config["mode"] == "named" and not (config["named_token"] and config["named_url"]):
            reasons.append("no_named_tunnel")
        return {"run": not reasons, "reasons": reasons, "config": config}

    async def reconcile(self) -> None:
        wanted = self.desired()
        config = wanted["config"]
        if self._retry_handled is None:
            self._retry_handled = config["retry_at"]
        if config["retry_at"] > self._retry_handled:

            self._retry_handled = config["retry_at"]
            if self.tunnel is not None:
                await self._stop_tunnel()
            self._download_retry_at = 0.0
            self._server_failures = 0
            self._server_retry_at = 0.0
            self._signature = ""
        signature = json.dumps([wanted["run"], config["mode"], config["custom_url"], config["port"],
                                config["cf_protocol"], config["cf_edge_ip"], config["named_url"],
                                config["local_fallback"],
                                hashlib.sha256(config["named_token"].encode()).hexdigest()[:12]])
        if signature != self._signature:
            # The settings changed (e.g. a different port): a fresh attempt.
            self._server_failures = 0
            self._server_retry_at = 0.0
        if await self._watchdog(config):
            return
        if signature == self._signature:
            settled = not wanted["run"] or (
                self.server_task is not None and not self.server_task.done()
                and (config["mode"] == "custom" or self.tunnel is not None
                     or (self._download_task is not None and not self._download_task.done())))
            if settled:
                self.write_status()
                return
        self._signature = signature
        if not wanted["run"]:
            await self.stop_all()
            reason = wanted["reasons"][0]
            self.mode = config["mode"]
            self._set_state("off" if reason == "disabled" else "blocked", reason)
            return
        self.mode = config["mode"]
        self.local_fallback = bool(config["local_fallback"])
        if self.server_task is not None and (self._preferred_port != config["port"]
                or (self.mode != "cloudflare" and self.port != config["port"])):
            await self.stop_all()
        # Wave #8: still inside the backoff after a failed bind — wait,
        # do not hammer the port every 2 s. The error state stays visible.
        if self.server_task is None and time.monotonic() < self._server_retry_at:
            return
        await self._ensure_server(config["port"])
        if self.server_task is None:
            return
        if config["mode"] == "custom":
            await self._stop_tunnel()
            self.url = config["custom_url"]
            self.verified = False
            self._set_state("online")

            self._start_verify(publish=False)
            return
        await self._stop_tunnel()
        self.url = config["named_url"] if config["mode"] == "named" else None
        await self._ensure_tunnel()

    async def _ensure_server(self, preferred: int) -> None:
        if self.server_task is not None and not self.server_task.done():
            return
        from .tgapp_api import create_tgapp
        self._preferred_port = preferred
        port = preferred
        # An external reverse proxy or a named tunnel already targets this port.
        if self.mode == "cloudflare" and not _port_free(preferred):
            port = pick_port(preferred + 1)
        server = _make_server(create_tgapp(), port)
        self.server, self.port = server, port
        self.server_task = asyncio.ensure_future(server.serve())
        for _ in range(100):
            if getattr(server, "started", False) or self.server_task.done():
                break
            await asyncio.sleep(0.05)
        if self.server_task.done() or not getattr(server, "started", False):
            task, self.server, self.server_task = self.server_task, None, None
            server.should_exit = True
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, 8)
            # Wave #8: do not clear _signature here — clearing it made
            # every reconcile look like a settings change and retried the
            # bind at once. Schedule the next attempt with backoff instead.
            self._server_failures += 1
            self._server_retry_at = time.monotonic() + min(
                120.0, 5.0 * 2 ** min(self._server_failures - 1, 5))
            self._set_state("error", f"Не удалось открыть порт {port}")
        else:
            self._server_failures = 0
            self._server_retry_at = 0.0

    async def _ensure_tunnel(self) -> None:
        if self.tunnel is not None and self.tunnel.port == self.port:
            self.tunnel.start()
            return
        await self._stop_tunnel()
        binary = find_cloudflared()
        if binary is None:
            if time.monotonic() < self._download_retry_at:
                return
            if self._download_task is None or self._download_task.done():
                self._download_task = asyncio.ensure_future(self._download_then_start())
            return
        self._start_tunnel(binary)

    async def _download_then_start(self) -> None:
        self._set_state("downloading")

        def progress(done: int, total: int) -> None:
            self.download = {"done": done, "total": total}

        try:
            binary = await download_cloudflared(progress)
        except Exception as exc:
            self._signature = ""
            self._download_retry_at = time.monotonic() + 90
            self._set_state("error", f"cloudflared не скачан: {exc}")
            return
        if self.desired()["run"] and self.mode in ("cloudflare", "named"):
            self._start_tunnel(binary)

    def _on_tunnel_state(self, state: str, error: str) -> None:
        """Tunnel state changes from CloudflaredTunnel.on_state.

        The last error is NOT cleared while the next attempt starts: wiping
        it on every "starting"/"connecting" is what made errors disappear
        from the settings card. It is cleared by "online" (via _verify) or
        by a newer error.
        """
        if state == "online":
            self._set_state("verifying", "")
            self._start_verify(publish=True)
            return
        self._cancel_verify()
        self.verified = False
        if state in ("starting", "connecting"):
            self._set_state(state, self.error)
        else:
            self._set_state(state, error)

    def _start_tunnel(self, binary: Path) -> None:
        config = load_config()
        named = config["mode"] == "named"

        def on_url(url: Optional[str]) -> None:
            if named:
                return
            if url != self.url:
                self.verified = False
            self.url = url
            self.write_status()
            self._notify()

        self.tunnel = CloudflaredTunnel(binary, self.port, on_url=on_url, on_state=self._on_tunnel_state,
                                        protocol=config["cf_protocol"], edge_ip_version=config["cf_edge_ip"],
                                        token=config["named_token"] if named else "")
        self.tunnel.start()

    def _cancel_verify(self) -> None:
        task, self._verify_task = self._verify_task, None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    def _start_verify(self, *, publish: bool) -> None:
        self._cancel_verify()
        self._verify_task = asyncio.ensure_future(self._verify(publish))

    async def _watchdog(self, config: Dict[str, Any]) -> bool:
        """Restart a live tunnel that stopped answering through the internet.

        Returns True when it restarted the tunnel (the caller should skip
        the rest of this round): the next round starts a fresh tunnel.
        """
        if self.state != "online" or config["mode"] == "custom":
            return False
        base = self.url or ""
        if not base or self.tunnel is None:
            return False
        now = time.monotonic()
        if now - self._health_checked_at < 60:
            return False
        self._health_checked_at = now
        try:
            kind, _detail = await self._probe(base)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - an inconclusive probe restarts nothing
            return False
        if kind == "ok":
            self._health_failures = 0
            self._health_restarts = 0
            return False
        if kind != "unreachable":
            # DNS (the address has not propagated yet) or HTTP (the local
            # server answers wrongly): not the tunnel's fault.
            self._health_failures = 0
            return False
        self._health_failures += 1
        if (self._health_failures < 3 or self._health_restarts >= 3
                or now - self._health_last_restart < 900):
            return False
        self._health_failures = 0
        self._health_restarts += 1
        self._health_last_restart = now
        await self._stop_tunnel()
        self._signature = ""
        return True

    async def _probe(self, base: str) -> Tuple[str, str]:
        if self.probe is not None:
            result = self.probe(base)
            return await result if asyncio.iscoroutine(result) else result
        import httpx
        async with httpx.AsyncClient(follow_redirects=False) as client:
            return await check_public_url(base, client)

    async def _verify(self, publish: bool) -> None:
        """Wait until the public address resolves (the system resolver) and
        /tgapp/healthz answers with our nonce, then publish it. A brand-new
        trycloudflare host often does not resolve yet on the phone's DNS:
        sending it to Telegram too early is what produced «server IP address
        could not be found». In local mode the public check is skipped
        entirely: the local listener works without any external DNS."""
        if self.local_fallback:
            self.verified, self.detail = False, ("Локальный режим: публичная проверка отключена — "
                                                 "работает локальный адрес")
            if publish:
                self._set_state("online", "")
            else:
                self.write_status()
            return
        base = self.url or ""
        deadline = time.monotonic() + VERIFY_TIMEOUT
        kind, detail = "http", ""
        while base and time.monotonic() < deadline:
            if self.local_fallback:
                self.verified, self.detail = False, ("Локальный режим: публичная проверка отключена — "
                                                     "работает локальный адрес")
                if publish:
                    self._set_state("online", "")
                else:
                    self.write_status()
                return
            try:
                kind, detail = await self._probe(base)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                kind, detail = "http", type(exc).__name__
            host = urllib.parse.urlsplit(base).hostname or ""
            self._last_check = {"host": host, "kind": kind, "detail": detail,
                                "checked_at": time.time(), "resolver": "system"}
            if self.url != base:
                return
            if kind == "ok":
                self.last_working_url = base
                self.verified, self.detail = True, ""
                try:
                    remember_working_url(base)
                except Exception:
                    pass
                if publish:
                    self._set_state("online", "")
                else:
                    self.write_status()
                return
            self.detail = detail
            if publish:
                self._set_state("dns_pending" if kind == "dns" else "verifying", "")
            else:
                self.write_status()
            await asyncio.sleep(VERIFY_EVERY)
        if not base or not publish:
            if base and not publish:
                self.detail = "Проверка через интернет не прошла: " + detail
                self.write_status()
            return
        # Even "dns": the probe only says the fresh address is not visible
        # from here yet (or DNS itself failed). Killing the tunnel on that
        # restarts the same failure forever; the address is published anyway
        # below and the tunnel is left alone.
        # "unreachable"/"http"/"dns": nothing could be concluded about DNS, or the
        # check failed for another network reason. The address is published
        # anyway below (with the detail kept), and the tunnel is left alone:
        # killing it on an inconclusive probe only restarts the same failure.

        self.verified = False
        self.detail = "Проверка через интернет не прошла (" + detail + ") — адрес всё равно отправлен в Telegram"
        self._set_state("online", "")

    async def _stop_tunnel(self) -> None:
        self._cancel_verify()
        tunnel, self.tunnel = self.tunnel, None
        if tunnel is not None:
            await tunnel.stop()
        if self._download_task is not None and not self._download_task.done():
            self._download_task.cancel()
        self._download_task = None

    async def stop_all(self) -> None:
        await self._stop_tunnel()
        server, task = self.server, self.server_task
        self.server, self.server_task = None, None
        if server is not None:
            server.should_exit = True
        if task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, 8)
        self.url = None
        self.port = 0

    def wake(self) -> None:
        # Wave #8: something changed — a retry is due now, not after the
        # port backoff.
        self._server_failures = 0
        self._server_retry_at = 0.0
        self._wake.set()

_MANAGER: Optional[Manager] = None

def notify_bot(url: Optional[str]) -> None:
    """Tell the bot about a new address; the bot module is optional."""
    try:
        from . import telegram_bot
    except Exception:
        return
    callback = getattr(telegram_bot, "on_webapp_url_changed", None)
    if not callable(callback):
        return
    try:
        result = callback(url)
        if asyncio.iscoroutine(result):
            task = asyncio.ensure_future(result)
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
    except Exception:
        log.exception("tgapp: on_webapp_url_changed failed")

async def run_manager(poll: float = 2.0) -> None:
    """Proxy background task: follow the settings until cancelled."""
    global _MANAGER
    manager = Manager()
    _MANAGER = manager
    try:
        while True:
            try:
                await manager.reconcile()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("tgapp: reconcile failed")
                manager._signature = ""
                manager._set_state("error", str(exc)[:300])
            try:
                await asyncio.wait_for(manager._wake.wait(), poll)
            except asyncio.TimeoutError:
                pass
            manager._wake.clear()
    finally:
        try:
            await manager.stop_all()
        finally:
            manager.state = "off"

            with contextlib.suppress(OSError):
                _write_json(status_path(), {**manager.snapshot(), "heartbeat": 0})
            if _MANAGER is manager:
                _MANAGER = None

def manager() -> Optional[Manager]:
    return _MANAGER

def read_status() -> Dict[str, Any]:
    """Status as the desktop window sees it (from the file the Proxy keeps fresh)."""
    if _MANAGER is not None:
        return _MANAGER.snapshot()
    data = _read_json(status_path())
    fresh = time.time() - float(data.get("heartbeat") or 0) < STATUS_FRESH_SECONDS
    if not fresh:
        return {"state": "proxy_offline", "error": "", "url": None, "port": 0,
                "mode": load_config()["mode"], "download": {"done": 0, "total": 0}}
    return data

def webapp_url() -> Optional[str]:
    """Public HTTPS URL of the Mini App (``…/tgapp/``) while it is reachable, else None."""
    if _MANAGER is not None:
        return _MANAGER.public_url()
    data = read_status()
    url = data.get("url")
    return str(url) if data.get("state") == "online" and url else None

def webapp_enabled() -> bool:
    """The user turned the Mini App on (the address may still be starting)."""
    try:
        return bool(load_config()["enabled"])
    except Exception:
        return False

def _status_diagnostics(status: Dict[str, Any], config: Dict[str, Any],
                        local_url: str, manager: Optional["Manager"] = None) -> Dict[str, Any]:
    """What the settings card shows when the public address cannot be
    verified: which host, which error, and the local fallback. Never raises."""
    if manager is not None:
        return manager._diagnostics(local_url)
    base = str(status.get("base") or status.get("url") or "")
    host = urllib.parse.urlsplit(base).hostname or ""
    detail = status.get("diagnostics") if isinstance(status.get("diagnostics"), dict) else {}
    return {"host": detail.get("host") or host,
            "error": detail.get("error") or status.get("detail") or status.get("error") or "",
            "kind": detail.get("kind") or "",
            "resolver": detail.get("resolver") or status.get("edge_source") or "system",
            "checked_at": detail.get("checked_at") or 0,
            "local_url": local_url,
            "last_working_url": detail.get("last_working_url") or str(config.get("last_working_url") or ""),
            "last_working_at": int(config.get("last_working_at") or 0),
            "local_fallback": bool(config.get("local_fallback")),
            "phase": detail.get("phase") or status.get("phase") or "",
            "edge_source": detail.get("edge_source") or status.get("edge_source") or "",
            "attempts_left": detail.get("attempts_left") if detail.get("attempts_left") is not None
                            else status.get("attempts_left") or 0,
            "dns_detail": detail.get("dns_detail") or status.get("dns_detail") or "",
            "last_error": detail.get("last_error") or status.get("last_error") or "",
            "last_error_en": detail.get("last_error_en") or status.get("last_error_en") or "",
            "last_error_at": detail.get("last_error_at") or status.get("last_error_at") or 0.0,
            "last_error_raw": detail.get("last_error_raw") or status.get("last_error_raw") or "",
            "last_error_kind": detail.get("last_error_kind") or status.get("last_error_kind") or "",
            "next_step": detail.get("next_step") or status.get("next_step") or "",
            "next_step_en": detail.get("next_step_en") or status.get("next_step_en") or "",
            "error_history": detail.get("error_history") or status.get("error_history") or [],
            "recent_lines": detail.get("recent_lines") or status.get("recent_lines") or []}

def desktop_status() -> Dict[str, Any]:
    """Everything the settings card in the desktop window shows.

    One unreachable network must never take the whole card down: every
    piece is read defensively, and the local address and the diagnostics
    are always present, even when the Proxy status file is stale."""
    try:
        config = load_config()
    except Exception:
        config = dict(DEFAULT_CONFIG)
    try:
        prefs = _preferences()
    except Exception:
        prefs = {}
    try:
        status = read_status()
    except Exception:
        status = {"state": "proxy_offline", "error": "", "url": None, "port": 0,
                  "mode": config.get("mode", "cloudflare"), "download": {"done": 0, "total": 0}}
    if not isinstance(status, dict):
        status = {"state": "proxy_offline", "error": "", "url": None, "port": 0,
                  "mode": config.get("mode", "cloudflare"), "download": {"done": 0, "total": 0}}
    problems = []
    try:
        if not bot_token(prefs):
            problems.append("no_token")
        if not authorized_user_ids(prefs):
            problems.append("no_user")
    except Exception:
        problems.append("no_token")
    try:
        if not (STATIC_DIR / "index.html").is_file():
            problems.append("no_static")
    except Exception:
        problems.append("no_static")
    try:
        cloudflared = bool(find_cloudflared())
    except Exception:
        cloudflared = False
    local_url = f"http://127.0.0.1:{status.get('port') or config.get('port') or DEFAULT_PORT}{ENTRY_PATH}"
    try:
        public = public_config(config)
    except Exception:
        public = {key: value for key, value in config.items() if key not in ("secret", "named_token")}
    if "diagnostics" not in status or not isinstance(status.get("diagnostics"), dict):
        status = {**status, "diagnostics": _status_diagnostics(status, config, local_url, _MANAGER)}
    return {"config": public, "status": status, "problems": problems,
            "cloudflared": cloudflared, "windows": os.name == "nt",
            "local_url": local_url,
            "diagnostics": status["diagnostics"]}
