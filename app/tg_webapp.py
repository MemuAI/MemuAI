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
CLOUDFLARED_MIN_BYTES = 8 * 1024 * 1024
CLOUDFLARED_MAX_BYTES = 200 * 1024 * 1024

TRYCLOUDFLARE_RE = re.compile(r"https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com\b", re.I)

MODES = ("cloudflare", "named", "custom")
PROTOCOLS = ("auto", "http2", "quic")
EDGE_IP_VERSIONS = ("auto", "4", "6")
NAMED_TOKEN_RE = re.compile(r"^[A-Za-z0-9+/=_-]{40,4096}$")


# --------------------------------------------------------------------------- files

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


# --------------------------------------------------------------------------- config

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "mode": "cloudflare",
    "custom_url": "",
    "port": DEFAULT_PORT,
    "max_age_hours": 24,
    # MemuCode CLI sessions may be watched and driven from the Mini App.
    "code_control": True,
    # Round 29: sign in from a normal browser (QR confirmed in the Mini App / one-time link from the bot).
    "web_login": True,
    "secret": "",
    "secret_created": 0,
    # Round 21: transport of cloudflared ("auto" starts with QUIC and falls
    # back to HTTP/2 after a failure — UDP/QUIC is often blocked), the edge IP
    # family, a named Cloudflare Tunnel (stable own domain) and a manual retry.
    "cf_protocol": "auto",
    "cf_edge_ip": "auto",
    "named_token": "",
    "named_url": "",
    "retry_at": 0,
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
    out["max_age_hours"] = max(1, min(24 * 7, hours))
    out["code_control"] = out.get("code_control") is not False
    out["web_login"] = out.get("web_login") is not False
    out["cf_protocol"] = out.get("cf_protocol") if out.get("cf_protocol") in PROTOCOLS else "auto"
    out["cf_edge_ip"] = str(out.get("cf_edge_ip")) if str(out.get("cf_edge_ip")) in EDGE_IP_VERSIONS else "auto"
    token = str(out.get("named_token") or "").strip()
    out["named_token"] = token if NAMED_TOKEN_RE.fullmatch(token) else ""
    out["named_url"] = normalize_custom_url(out.get("named_url"))
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
                                              "named_url")
                if isinstance(updates, dict) and key in updates}
    for key in ("custom_url", "named_url"):
        if key in accepted and str(accepted[key] or "").strip() and not normalize_custom_url(accepted[key]):
            raise ValueError("Адрес должен начинаться с https:// и не содержать параметров")
    if "named_token" in accepted:
        token = str(accepted["named_token"] or "").strip()
        if token.startswith("cloudflared"):
            # People paste the whole "cloudflared service install <token>" line.
            token = token.split()[-1]
        if token and not NAMED_TOKEN_RE.fullmatch(token):
            raise ValueError("Это не похоже на токен Cloudflare Tunnel (длинная строка из Zero Trust → Tunnels)")
        accepted["named_token"] = token
    if isinstance(updates, dict) and updates.get("retry"):
        accepted["retry_at"] = time.time()
    if "enabled" in accepted:
        accepted["enabled"] = accepted["enabled"] is True or str(accepted["enabled"]).lower() == "true"
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


# --------------------------------------------------------------------------- preferences

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
    return text if re.fullmatch(r"\d{1,20}", text) else ""      # users have positive ids; groups are negative


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
    # A dedicated paired-user field, if some settings provide it, is always allowed too.
    for key in ("telegram_user_id", "telegram_paired_user_id", "telegram_paired_user"):
        ids.append(_user_id_text(prefs.get(key)))
    return [x for x in dict.fromkeys(ids) if x]


def is_authorized_user(user_id: Any, prefs: Optional[Dict[str, Any]] = None) -> bool:
    text = _user_id_text(user_id)
    return bool(text) and text in authorized_user_ids(prefs)


# --------------------------------------------------------------------------- initData

# English texts of ``InitDataError`` (the Russian one is the exception message).
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


# --------------------------------------------------------------------------- session tokens

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
    if float(data.get("e") or 0) < (time.time() if now is None else now):
        return None
    return data


# --------------------------------------------------------------------------- rate limiting

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


# --------------------------------------------------------------------------- tunnel output

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


#: Round 27: the tunnel's status texts (``error``, ``detail``) are composed in Russian from many pieces
#: («cloudflared не скачан: Автоматическая загрузка… · 3 попыток подряд…») and reached the English window
#: as is. Every fragment has its English twin here; ``status_en`` swaps them piece by piece, so composed
#: messages translate whole and cloudflared's own English log lines pass through untouched.
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
    (r"Компьютер не может найти серверы Cloudflare \(DNS\) — проверьте интернет или VPN",
     "The computer cannot find Cloudflare's servers (DNS) — check the internet or VPN"),
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
    if "invalid tunnel secret" in lowered or "unauthorized" in lowered and "tunnel" in lowered:
        return "Cloudflare отклонил токен именованного туннеля — скопируйте его заново в Zero Trust → Tunnels"
    if "config.yaml" in lowered or "cannot determine default configuration" in lowered:
        return ("cloudflared нашёл свой config.yaml: быстрый туннель с ним не запускается. "
                "Переименуйте %USERPROFILE%\\.cloudflared\\config.yaml или укажите свой адрес.")
    if "too many requests" in lowered or "429" in lowered:
        return "Cloudflare временно ограничил создание быстрых туннелей — повтор через минуту"
    if "failed to request quick tunnel" in lowered or "failed to unmarshal quick tunnel" in lowered:
        return "Cloudflare не выдал адрес туннеля — нет интернета, сервис недоступен или заблокирован"
    if "quic" in lowered and ("failed to dial" in lowered or "timeout" in lowered or "no recent network activity" in lowered):
        return "Протокол QUIC (UDP) не проходит — переключаюсь на HTTP/2"
    if "no such host" in lowered or "lookup " in lowered and "dns" in lowered or "server misbehaving" in lowered:
        return "Компьютер не может найти серверы Cloudflare (DNS) — проверьте интернет или VPN"
    if "tls handshake timeout" in lowered or "certificate" in lowered:
        return "Не удалось установить защищённое соединение с Cloudflare — мешает антивирус, прокси или блокировка"
    if "i/o timeout" in lowered or "context deadline exceeded" in lowered or "connection refused" in lowered \
            or "connection reset" in lowered or "network is unreachable" in lowered:
        return "Нет соединения с Cloudflare — возможна блокировка; попробуйте HTTP/2, VPN или свой адрес"
    return ""


def _quic_trouble(line: str) -> bool:
    lowered = str(line or "").lower()
    return "quic" in lowered and ("failed" in lowered or "timeout" in lowered or "no recent network activity" in lowered)


# --------------------------------------------------------------------------- cloudflared binary

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
        except Exception as exc:  # the API is rate limited; the plain URL still works
            log.info("tgapp: release metadata unavailable (%s)", type(exc).__name__)
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
        if digest and hasher.hexdigest() != digest:
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


# --------------------------------------------------------------------------- tunnel process

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
                 url_timeout: float = 45.0, register_timeout: float = 75.0, max_failures: int = 6) -> None:
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
        self.url: Optional[str] = None
        self.registered = False
        self.process: Any = None
        self.failures = 0
        self.last_error = ""
        self.retry_at = 0.0
        self.gave_up = False
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self._retry = asyncio.Event()
        self.lines: Deque[str] = deque(maxlen=200)

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
        """Manual «Retry» from Settings: forget the failures and start now."""
        self.failures = 0
        self.gave_up = False
        self._retry.set()
        self.start()

    @staticmethod
    def backoff(failures: int) -> float:
        return float(min(120, 2 ** max(1, min(failures, 7))))

    async def _run(self) -> None:
        while not self._stopping:
            started = time.monotonic()
            self.last_error = ""
            code = await self._run_once()
            if self._stopping:
                break
            if time.monotonic() - started > 300:
                self.failures = 0                       # it ran fine for a while: start the backoff again
            self.failures += 1
            if not self.registered and self.protocol == "auto" and not self.use_http2:
                # QUIC (UDP 7844) is the usual victim of firewalls and DPI.
                self.use_http2 = True
            if self.failures >= self.max_failures:
                self.gave_up = True
                self.retry_at = 0.0
                self.on_state("failed", (self.last_error or f"cloudflared завершился (код {code})") +
                              f" · {self.failures} попыток подряд — нажмите «Повторить»")
                self._retry.clear()
                await self._retry.wait()
                continue
            delay = self.backoff(self.failures)
            self.retry_at = time.time() + delay
            self.on_state("error", (self.last_error or f"cloudflared завершился (код {code})") +
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
        self.on_state("starting", "")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        kwargs: Dict[str, Any] = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                      creationflags=flags, cwd=str(bin_dir()))
        if self.named:
            kwargs["env"] = {**os.environ, "TUNNEL_TOKEN": self.token}
        try:
            self.process = self.popen(self.command(), **kwargs)
        except (OSError, ValueError) as exc:
            self.last_error = f"cloudflared не запустился: {exc}"
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
                        self.last_error = self.last_error or reason
                        self._terminate()
                        break
                try:
                    line = await asyncio.wait_for(queue.get(), timeout) if timeout else await queue.get()
                except asyncio.TimeoutError:
                    self.last_error = self.last_error or reason
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
        hint = _tunnel_error_hint(line)
        if hint and not self.registered:
            self.last_error = hint
        if _quic_trouble(line) and self.protocol == "auto":
            self.use_http2 = True
        url = None if self.named else parse_tunnel_url(line)
        if url and not self.url:
            self.url = url
            self.last_error = ""
            self.on_state("connecting", "")
        if (self.url or self.named) and not self.registered and _is_registered_line(line):
            self.registered = True
            self.failures = 0
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
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if self.url is not None:
            self.url = None
            self.on_url(None)


# --------------------------------------------------------------------------- public reachability

DOH_ENDPOINTS = ("https://1.1.1.1/dns-query", "https://8.8.8.8/resolve")
VERIFY_TIMEOUT = 150.0
VERIFY_EVERY = 4.0
_NONCE = secrets.token_hex(8)


def instance_nonce() -> str:
    """Random per process: /tgapp/healthz echoes it, so a check over the
    public URL proves the answer came from *this* listener."""
    return _NONCE


async def doh_resolve(host: str, client: Any) -> Optional[bool]:
    """Does ``host`` resolve in public DNS? True/False, None when no DoH
    resolver could be reached (blocked network) — then the system resolver decides."""
    answered = False
    for endpoint in DOH_ENDPOINTS:
        for record in ("A", "AAAA"):
            try:
                response = await client.get(endpoint, params={"name": host, "type": record},
                                            headers={"accept": "application/dns-json"}, timeout=6)
                data = response.json()
            except Exception:
                continue
            answered = True
            if int(data.get("Status", 2)) == 0 and any(int(row.get("type", 0)) in (1, 28)
                                                       for row in data.get("Answer") or []):
                return True
    if answered:
        return False
    try:
        await asyncio.to_thread(socket.getaddrinfo, host, 443)
        return True
    except OSError:
        return None


async def check_public_url(base: str, client: Any) -> Tuple[str, str]:
    """One reachability probe of the public address: ("ok"|"dns"|"http", detail)."""
    host = urllib.parse.urlsplit(base).hostname or ""
    resolved = await doh_resolve(host, client)
    if resolved is False:
        return "dns", "Адрес ещё не появился в DNS — жду"
    try:
        response = await client.get(base.rstrip("/") + "/tgapp/healthz", timeout=10)
        data = response.json() if response.status_code == 200 else {}
    except Exception as exc:
        return ("dns" if resolved is None else "http"), f"Не открывается через интернет: {type(exc).__name__}"
    if data.get("app") == "memuai-tgapp" and data.get("nonce") == instance_nonce():
        return "ok", ""
    return "http", f"Адрес отвечает, но не MemuAI (HTTP {response.status_code})"


# --------------------------------------------------------------------------- listener

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

        def install_signal_handlers(self) -> None:  # older uvicorn
            return None

    return _QuietServer(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None,
                                       access_log=False, lifespan="off",
                                       timeout_keep_alive=20, ws="none"))


# --------------------------------------------------------------------------- manager

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
        self._notified: Any = object()
        self._wake = asyncio.Event()
        self.detail = ""
        self.retry_at = 0.0
        self.verified = False
        self._verify_task: Optional[asyncio.Task] = None
        self._retry_handled: Optional[float] = None
        # Injectable for tests: probe(base_url) -> ("ok"|"dns"|"http", detail).
        self.probe: Optional[Callable[[str], Any]] = None

    # status -------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        tunnel = self.tunnel
        return {"pid": os.getpid(), "heartbeat": time.time(), "state": self.state, "error": self.error,
                "error_en": status_en(self.error), "detail_en": status_en(self.detail),
                "url": self.public_url(), "base": self.url or "", "port": self.port, "mode": self.mode,
                "download": dict(self.download), "detail": self.detail, "verified": self.verified,
                "retry_at": (tunnel.retry_at if tunnel is not None else 0.0) or self.retry_at,
                "protocol": (tunnel.effective_protocol() or "auto") if tunnel is not None else "",
                "failures": tunnel.failures if tunnel is not None else 0}

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

    # reconcile ----------------------------------------------------------
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
            # «Retry» in Settings: a brand-new tunnel (new address, fresh failure count).
            self._retry_handled = config["retry_at"]
            if self.tunnel is not None:
                await self._stop_tunnel()
            self._download_retry_at = 0.0
            self._signature = ""
        signature = json.dumps([wanted["run"], config["mode"], config["custom_url"], config["port"],
                                config["cf_protocol"], config["cf_edge_ip"], config["named_url"],
                                hashlib.sha256(config["named_token"].encode()).hexdigest()[:12]])
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
        if self.server_task is not None and self._preferred_port != config["port"]:
            await self.stop_all()                          # the user moved the listener to another port
        await self._ensure_server(config["port"])
        if self.server_task is None:
            return
        if config["mode"] == "custom":
            await self._stop_tunnel()
            self.url = config["custom_url"]
            self.verified = False
            self._set_state("online")
            # Diagnostics only: the user's own address is published at once.
            self._start_verify(publish=False)
            return
        await self._stop_tunnel()            # mode/protocol/token changed: a fresh process
        self.url = config["named_url"] if config["mode"] == "named" else None
        await self._ensure_tunnel()

    async def _ensure_server(self, preferred: int) -> None:
        if self.server_task is not None and not self.server_task.done():
            return
        from .tgapp_api import create_tgapp
        self._preferred_port = preferred
        port = preferred if _port_free(preferred) else pick_port(preferred + 1)
        server = _make_server(create_tgapp(), port)
        self.server, self.port = server, port
        self.server_task = asyncio.ensure_future(server.serve())
        for _ in range(100):
            if getattr(server, "started", False) or self.server_task.done():
                break
            await asyncio.sleep(0.05)
        if self.server_task.done() or not getattr(server, "started", False):
            self.server_task = None
            self._signature = ""
            self._set_state("error", f"Не удалось открыть порт {port}")

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

    def _start_tunnel(self, binary: Path) -> None:
        config = load_config()
        named = config["mode"] == "named"

        def on_url(url: Optional[str]) -> None:
            if named:
                return                      # the named address is fixed
            if url != self.url:
                self.verified = False
            self.url = url
            self.write_status()
            self._notify()

        def on_state(state: str, error: str) -> None:
            if state == "online":
                # cloudflared is connected, but the address is published to
                # Telegram only after it resolves in public DNS and answers.
                self._set_state("verifying", "")
                self._start_verify(publish=True)
                return
            self._cancel_verify()
            self.verified = False
            self._set_state(state, error)

        self.tunnel = CloudflaredTunnel(binary, self.port, on_url=on_url, on_state=on_state,
                                        protocol=config["cf_protocol"], edge_ip_version=config["cf_edge_ip"],
                                        token=config["named_token"] if named else "")
        self.tunnel.start()

    # public reachability -------------------------------------------------
    def _cancel_verify(self) -> None:
        task, self._verify_task = self._verify_task, None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    def _start_verify(self, *, publish: bool) -> None:
        self._cancel_verify()
        self._verify_task = asyncio.ensure_future(self._verify(publish))

    async def _probe(self, base: str) -> Tuple[str, str]:
        if self.probe is not None:
            result = self.probe(base)
            return await result if asyncio.iscoroutine(result) else result
        import httpx
        async with httpx.AsyncClient(follow_redirects=False) as client:
            return await check_public_url(base, client)

    async def _verify(self, publish: bool) -> None:
        """Wait until the public address resolves (DoH 1.1.1.1 / 8.8.8.8) and
        /tgapp/healthz answers with our nonce, then publish it. A brand-new
        trycloudflare host often does not resolve yet on the phone's DNS:
        sending it to Telegram too early is what produced «server IP address
        could not be found»."""
        base = self.url or ""
        deadline = time.monotonic() + VERIFY_TIMEOUT
        kind, detail = "http", ""
        while base and time.monotonic() < deadline:
            try:
                kind, detail = await self._probe(base)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                kind, detail = "http", type(exc).__name__
            if self.url != base:
                return                                  # a newer address took over
            if kind == "ok":
                self.verified, self.detail = True, ""
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
        if kind == "dns":
            # This host never appeared in DNS: ask cloudflared for a new one.
            self._set_state("error", "Адрес туннеля так и не появился в DNS — запрашиваю новый")
            tunnel = self.tunnel
            if tunnel is not None and not tunnel.named:
                tunnel._terminate()
            return
        # It resolves, but our own check could not reach it (the PC's network
        # may differ from the phone's): publish, and say so.
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
            # Round 26: the Proxy is going away — a fresh «off» heartbeat made the
            # window show «Starting…» for 20 s instead of «MemuAI Proxy is not running».
            with contextlib.suppress(OSError):
                _write_json(status_path(), {**manager.snapshot(), "heartbeat": 0})
            if _MANAGER is manager:
                _MANAGER = None


def manager() -> Optional[Manager]:
    return _MANAGER


# --------------------------------------------------------------------------- public contract

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


def desktop_status() -> Dict[str, Any]:
    """Everything the settings card in the desktop window shows."""
    config = load_config()
    prefs = _preferences()
    status = read_status()
    problems = []
    if not bot_token(prefs):
        problems.append("no_token")
    if not authorized_user_ids(prefs):
        problems.append("no_user")
    if not (STATIC_DIR / "index.html").is_file():
        problems.append("no_static")
    return {"config": public_config(config), "status": status, "problems": problems,
            "cloudflared": bool(find_cloudflared()), "windows": os.name == "nt",
            "local_url": f"http://127.0.0.1:{status.get('port') or config['port']}{ENTRY_PATH}"}
