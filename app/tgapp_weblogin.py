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

import secrets
import threading
import time
from typing import Any, Dict, Optional

TTL = 300
LINK_TTL = 300
WEB_SESSION_TTL = 12 * 3600
MAX_PENDING = 64
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
QR_PREFIX = "memuai-login:"

_lock = threading.Lock()
_requests: Dict[str, Dict[str, Any]] = {}
_codes: Dict[str, str] = {}
_links: Dict[str, Dict[str, Any]] = {}

def _prune(now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    for request_id, row in list(_requests.items()):
        if row["expires"] < now - 60:
            _requests.pop(request_id, None)
            _codes.pop(row["code"], None)
    for nonce, row in list(_links.items()):
        if row["expires"] < now:
            _links.pop(nonce, None)

def normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower().startswith(QR_PREFIX):
        text = text[len(QR_PREFIX):]
    text = "".join(ch for ch in text.upper() if ch.isalnum())
    return text[:8] if len(text) >= 8 else ""

def _new_code() -> str:
    while True:
        code = "".join(secrets.choice(ALPHABET) for _ in range(8))
        if code not in _codes:
            return code

def start(ip: str, agent: str, *, now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    with _lock:
        _prune(now)
        pending = [row for row in _requests.values() if row["status"] == "pending" and row["expires"] > now]
        if len(pending) >= MAX_PENDING:
            raise OverflowError("too many sign-in requests")
        request_id = secrets.token_urlsafe(32)
        code = _new_code()
        _requests[request_id] = {"code": code, "status": "pending", "created": now, "expires": now + TTL,
                                 "ip": str(ip or "")[:64], "agent": str(agent or "")[:300], "user": ""}
        _codes[code] = request_id
    return {"request_id": request_id, "code": code, "display": code[:4] + "-" + code[4:],
            "qr": QR_PREFIX + code, "expires": int(now + TTL), "ttl": TTL}

def _by_code(code: str, now: float) -> Optional[Dict[str, Any]]:
    request_id = _codes.get(normalize_code(code))
    row = _requests.get(request_id or "")
    if row is None or row["expires"] < now or row["status"] != "pending":
        return None
    return row

def inspect(code: str, *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    now = time.time() if now is None else now
    with _lock:
        _prune(now)
        row = _by_code(code, now)
        if row is None:
            return None
        return {"ip": row["ip"], "agent": row["agent"], "created": row["created"], "expires": row["expires"]}

def decide(code: str, user_id: str, approve: bool, *, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    with _lock:
        _prune(now)
        row = _by_code(code, now)
        if row is None:
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
        _codes.pop(row["code"], None)
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
    with _lock:
        _requests.clear()
        _codes.clear()
        _links.clear()

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
        return (tr("🌐 Mini App сейчас недоступен: включите его на компьютере (MemuAI → Настройки → Telegram → Mini App).",
                   "🌐 The Mini App is not available now: turn it on on the computer (MemuAI → Settings → Telegram → Mini App)."), None)
    if not enabled:
        return (tr("🌐 Вход из браузера выключен на компьютере: MemuAI → Настройки → Telegram → Mini App → «Вход из браузера».",
                   "🌐 Browser sign-in is off on the computer: MemuAI → Settings → Telegram → Mini App → “Browser sign-in”."), None)
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
