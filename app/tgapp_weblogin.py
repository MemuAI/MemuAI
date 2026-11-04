"""Round 29: the Mini App in a normal browser — sign-in confirmed from the user's own Telegram.

Inside Telegram the Mini App proves who it is with signed ``initData``. A normal browser (someone else's PC,
a tablet without Telegram) has nothing like that, and the Mini App must not become a page that anybody who
learns its address can open. Two ways in, both requiring the paired Telegram account:

1. **QR / code (device flow, like «sign in on another device»).** The browser asks for a login request
   (``weblogin.start``) and gets a secret ``request_id`` (kept only by that browser) and a short display code,
   shown as a QR code and as text. In the Mini App on the phone — already signed in by Telegram — the user
   scans the QR (``Telegram.WebApp.showScanQrPopup``) or types the code, sees *which* browser asks (address,
   browser, time) and approves (``weblogin.approve``). The browser polls (``weblogin.poll``) and receives an
   ordinary session token exactly once.
2. **One-time link from the bot** (``/web``): ``<url>#login=<nonce>`` — single use, 5 minutes, redeemed with
   ``weblogin.redeem``. The secret is in the URL fragment, so it never reaches a server log or a Referer.

Codes expire after 5 minutes, failed code look-ups are rate-limited per user, outstanding requests are
capped, a request is consumed when the token is handed over, and «Reset sessions» on the computer
(``regenerate_secret``) revokes every browser session too. Switched off with ``tgapp.json: web_login``.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional

TTL = 300
LINK_TTL = 300
WEB_SESSION_TTL = 12 * 3600
MAX_PENDING = 64
MAX_PENDING_PER_IP = 4  # one browser network cannot hog all login slots
STARTS_PER_IP = 10  # new login requests per address per START_WINDOW
START_WINDOW = 600
FAIL_LIMIT_USER = 10  # failed code look-ups per user per FAIL_WINDOW -> locked out
FAIL_LIMIT_GLOBAL = 50  # failed look-ups in total per FAIL_WINDOW -> global lockout
FAIL_WINDOW = 600
FAIL_LOCKOUT = 300
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
QR_PREFIX = "memuai-login:"

_lock = threading.Lock()
_requests: Dict[str, Dict[str, Any]] = {}
_links: Dict[str, Dict[str, Any]] = {}
_starts: Dict[str, Deque[float]] = {}  # ip -> start timestamps (rate limit)
_failures: Dict[str, Deque[float]] = {}  # key (user) -> failed look-up timestamps
_global_failures: Deque[float] = deque()
_locked_until = 0.0  # global inspect/approve lockout (a guessing flood burns nothing, it waits)

def _prune(now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    for request_id, row in list(_requests.items()):
        if row["expires"] < now - 60:
            _requests.pop(request_id, None)
    for nonce, row in list(_links.items()):
        if row["expires"] < now:
            _links.pop(nonce, None)
    for ip, hits in list(_starts.items()):
        while hits and now - hits[0] > START_WINDOW:
            hits.popleft()
        if not hits:
            _starts.pop(ip, None)
    for key, hits in list(_failures.items()):
        while hits and now - hits[0] > FAIL_WINDOW:
            hits.popleft()
        if not hits:
            _failures.pop(key, None)

def normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower().startswith(QR_PREFIX):
        text = text[len(QR_PREFIX):]
    text = "".join(ch for ch in text.upper() if ch.isalnum())
    return text[:8] if len(text) >= 8 else ""

def _hash(code: str, salt: str) -> str:
    return hashlib.sha256((salt + ":" + normalize_code(code)).encode("utf-8")).hexdigest()


def _code_taken(code: str, now: float) -> bool:
    for row in _requests.values():
        if row["status"] == "pending" and row["expires"] > now and hmac.compare_digest(
                _hash(code, row.get("salt") or ""), row.get("code_hash") or ""):
            return True
    return False


def _new_code(now: float) -> str:
    while True:
        code = "".join(secrets.choice(ALPHABET) for _ in range(8))
        if not _code_taken(code, now):
            return code

def start(ip: str, agent: str, *, now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    ip = str(ip or "")[:64]
    with _lock:
        _prune(now)
        starts = _starts.setdefault(ip, deque())
        if len(starts) >= STARTS_PER_IP:
            raise OverflowError("too many sign-in requests from this address")
        pending = [row for row in _requests.values() if row["status"] == "pending" and row["expires"] > now]
        if len(pending) >= MAX_PENDING:
            raise OverflowError("too many sign-in requests")
        if sum(1 for row in pending if row.get("ip") == ip) >= MAX_PENDING_PER_IP:
            raise OverflowError("too many sign-in requests from this address")
        request_id = secrets.token_urlsafe(32)
        code = _new_code(now)
        salt = secrets.token_hex(8)
        _requests[request_id] = {"code_hash": _hash(code, salt), "salt": salt, "status": "pending",
                                 "created": now, "expires": now + TTL,
                                 "ip": ip, "agent": str(agent or "")[:300], "user": ""}
        starts.append(now)
    return {"request_id": request_id, "code": code, "display": code[:4] + "-" + code[4:],
            "qr": QR_PREFIX + code, "expires": int(now + TTL), "ttl": TTL}

def _by_code(code: str, now: float) -> Optional[Dict[str, Any]]:
    # Codes are stored only as salted hashes; a look-up hashes the candidate
    # against every pending request (at most MAX_PENDING of them).
    code = normalize_code(code)
    if not code:
        return None
    for row in _requests.values():
        if row["status"] == "pending" and row["expires"] > now and hmac.compare_digest(
                _hash(code, row.get("salt") or ""), row.get("code_hash") or ""):
            return row
    return None


def _failure_locked(key: str, now: float) -> bool:
    if _locked_until > now:
        return True
    hits = _failures.get(key)
    if hits:
        while hits and now - hits[0] > FAIL_WINDOW:
            hits.popleft()
        if len(hits) >= FAIL_LIMIT_USER:
            return True
    return False


def _record_failure(key: str, now: float) -> None:
    global _locked_until
    _failures.setdefault(key, deque()).append(now)
    _global_failures.append(now)
    while _global_failures and now - _global_failures[0] > FAIL_WINDOW:
        _global_failures.popleft()
    if len(_global_failures) >= FAIL_LIMIT_GLOBAL:
        _locked_until = now + FAIL_LOCKOUT
        _global_failures.clear()

def inspect(code: str, *, key: str = "", now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    now = time.time() if now is None else now
    with _lock:
        _prune(now)
        if _failure_locked(key, now):
            return None
        row = _by_code(code, now)
        if row is None:
            _record_failure(key, now)
            return None
        return {"ip": row["ip"], "agent": row["agent"], "created": row["created"], "expires": row["expires"]}

def decide(code: str, user_id: str, approve: bool, *, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    key = str(user_id)
    with _lock:
        _prune(now)
        if _failure_locked(key, now):
            return False
        row = _by_code(code, now)
        if row is None:
            _record_failure(key, now)
            return False
        row["status"] = "approved" if approve else "denied"
        row["user"] = str(user_id) if approve else ""
        row["decided"] = now
        return True

def poll(request_id: str, issue: Any, *, now: Optional[float] = None) -> Dict[str, Any]:
    """``issue(user_id) -> (token, expires)`` is called once, when the request was approved."""
    now = time.time() if now is None else now
    with _lock:
        _prune(now)
        row = _requests.get(str(request_id or ""))
        if row is None:
            return {"status": "expired"}
        if row["status"] == "pending":
            if row["expires"] < now:
                return {"status": "expired"}
            return {"status": "pending", "expires": int(row["expires"])}
        _requests.pop(str(request_id), None)
        if row["status"] != "approved":
            return {"status": "denied"}
        user = row["user"]
    token, expires = issue(user)
    return {"status": "approved", "token": token, "expires": expires}

def issue_link(user_id: str, *, now: Optional[float] = None) -> str:
    now = time.time() if now is None else now
    nonce = secrets.token_urlsafe(24)
    with _lock:
        _prune(now)
        _links[nonce] = {"user": str(user_id), "expires": now + LINK_TTL}
    return nonce

def redeem(nonce: str, *, now: Optional[float] = None) -> str:
    """The user a one-time link was issued for ('' when unknown, used or expired)."""
    now = time.time() if now is None else now
    with _lock:
        _prune(now)
        row = _links.pop(str(nonce or ""), None)
    if row is None or row["expires"] < now:
        return ""
    return row["user"]

def reset() -> None:
    global _locked_until
    with _lock:
        _requests.clear()
        _links.clear()
        _starts.clear()
        _failures.clear()
        _global_failures.clear()
        _locked_until = 0.0

def bot_message(prefs: Dict[str, Any], user_id: str, url: Optional[str]) -> tuple:
    """Text and keyboard of the bot's ``/web`` answer: a one-time sign-in link for a normal browser."""
    en = str(prefs.get("telegram_language") or prefs.get("language") or "ru") == "en"

    def tr(ru: str, english: str) -> str:
        return english if en else ru

    try:
        from .tg_webapp import load_config
        enabled = load_config().get("web_login") is not False
    except Exception:
        enabled = False
    if not url:
        return (tr("🌐 Mini App сейчас недоступен: включите его на компьютере (MemuAI → Настройки → Мессенджеры → Telegram → Mini App).",
                   "🌐 The Mini App is not available now: turn it on on the computer (MemuAI → Settings → Messengers → Telegram → Mini App)."), None)
    if not enabled:
        return (tr("🌐 Вход из браузера выключен на компьютере: MemuAI → Настройки → Мессенджеры → Telegram → Mini App → «Вход из браузера».",
                   "🌐 Browser sign-in is off on the computer: MemuAI → Settings → Messengers → Telegram → Mini App → “Browser sign-in”."), None)
    nonce = issue_link(user_id)
    link = url.split("#", 1)[0] + "#login=" + nonce
    text = tr(
        "🌐 <b>MemuAI в браузере</b>\nОдноразовая ссылка для входа, действует 5 минут — не пересылайте её никому.\n\n"
        "На чужом компьютере проще так: откройте там адрес Mini App и отсканируйте QR-код в Mini App на телефоне "
        "(Настройки → «Войти на другом устройстве»).",
        "🌐 <b>MemuAI in a browser</b>\nA one-time sign-in link, valid for 5 minutes — do not forward it to anyone.\n\n"
        "On another computer it is easier to open the Mini App address there and scan the QR code with the Mini App "
        "on your phone (Settings → “Sign in on another device”).")
    markup = {"inline_keyboard": [[{"text": tr("🌐 Открыть в браузере", "🌐 Open in a browser"), "url": link}]]}
    return text, markup
