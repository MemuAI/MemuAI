"""Провайдер Gemini Web: gemini.google.com как OpenAI API (переписан в раунде 37).

Протокол — app/providers/geminiweb_proto.py (там же источники), импорт кук —
app/providers/geminiweb_cookies.py. Поток запроса:

1. GET gemini.google.com[/u/N]/app с куками аккаунта → SNlM0e (at), cfb2h (bl), FdrFJe (f.sid), qKIAYe (push id).
   Без кук страница тоже открывается (гостевой режим, только Flash; Google может показать капчу).
2. POST …/StreamGenerate?bl=…&f.sid=…&hl=en&_reqid=…&rt=c с f.req и at; заголовки X-Same-Domain,
   x-goog-ext-525005358-jspb (uuid запроса) и, для вошедших, x-goog-ext-525001261-jspb (выбор модели).
3. Поток кадров «<длина>\\n[…]» → текст c[1][0], мысли c[37][0][0] (→ reasoning), картинки c[12].
4. Set-Cookie из каждого ответа Google и ротация __Secure-1PSIDTS через accounts.google.com/RotateCookies
   (в фоне раз в ~10 минут) сохраняются обратно в аккаунт — куки не «протухают через час».

У провайдера свои HTTP-сессии на каждый аккаунт: общий пул app.providers.http держит один
cookie-jar на всех, и куки одного Google-аккаунта (или гостевые) смешивались бы с чужими.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
import urllib.parse
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from .base import (
    Field,
    Provider,
    ProviderAuthError,
    ProviderContentFilterError,
    ProviderError,
    ProviderModelUnavailableError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderServerError,
    normalize_messages,
    spawn_background,
    transcript_prompt,
)
from ..config import config_store
from . import http
from . import geminiweb_limits as limits
from . import geminiweb_media as media
from . import geminiweb_proto as proto
from .geminiweb_cookies import PSID, PSIDTS, parse_cookie_import, parse_header, set_cookie_value

BASE = proto.BASE
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
PAGE_TTL = 30 * 60
ROTATE_EVERY = 10 * 60
ROTATE_MIN_GAP = 60

LIMITS_TTL = 120
MODELS_TTL = 6 * 3600

MODELS: Dict[str, Dict[str, Any]] = {
    "gemini-3.8-flash": {"family": "flash", "mode": 1, "display": "3.8 Flash",
                         "note": "Gemini 3.8 Flash · everyday model (works without sign-in)"},
    "gemini-3.5-flash-lite": {"family": "lite", "mode": 6, "display": "3.5 Flash-Lite",
                              "note": "Gemini 3.5 Flash-Lite · fastest and lightest"},
    "gemini-3.1-pro": {"family": "pro", "mode": 3, "display": "3.1 Pro",
                       "note": "Gemini 3.1 Pro · needs a signed-in Google account"},
    "gemini-4-argon": {"family": "argon", "mode": 0, "display": "4 Argon", "paid": True,
                       "note": "Gemini 4 Argon · Google AI Pro / Ultra only"},
}

LEGACY: Dict[str, Tuple[str, bool]] = {
    "gemini-3.7-flash": ("gemini-3.8-flash", False),
    "gemini-3.6-flash": ("gemini-3.8-flash", False),
    "gemini-3.5-flash": ("gemini-3.8-flash", False),
    "gemini-3.5-flash-thinking": ("gemini-3.8-flash", False),
    "gemini-3.5-flash-thinking-lite": ("gemini-3.8-flash", False),
    "gemini-auto": ("gemini-3.8-flash", False),
    "gemini-flash": ("gemini-3.8-flash", False),
    "gemini-flash-lite": ("gemini-3.5-flash-lite", False),
    "gemini-pro": ("gemini-3.1-pro", False),
    "gemini-3.1-pro-thinking": ("gemini-3.1-pro", True),
}
DEFAULT_MODEL = "gemini-3.8-flash"
_THINK_SUFFIX_RE = re.compile(r"@think=([0-4])\s*$", re.IGNORECASE)
_EFFORT_SUFFIX_RE = re.compile(r"@effort=([a-z]+)\s*$", re.IGNORECASE)

EFFORTS: Dict[str, Tuple[int, bool]] = {"low": (4, False), "medium": (0, False), "high": (0, True)}
EFFORT_ALIASES = {"off": "low", "minimal": "low", "low": "low", "fast": "low",
                  "medium": "medium", "standard": "medium", "default": "medium", "on": "medium",
                  "high": "high", "xhigh": "high", "max": "high", "extended": "high", "deep": "high"}

def effort_level(value: Any) -> str:
    """Уровень из любого словаря (OpenAI reasoning_effort, наши уровни, суффикс) → low | medium | high | ""."""
    return EFFORT_ALIASES.get(str(value or "").strip().lower(), "")

def _family(text: str) -> str:
    low = (text or "").lower()
    for key in ("argon", "lite", "pro", "thinking"):
        if key in low:
            return key
    return "flash" if ("flash" in low or "fast" in low) else low

def _sapisidhash(sapisid: str, origin: str = BASE) -> str:
    ts = int(time.time())
    digest = hashlib.sha1(f"{ts} {sapisid} {origin}".encode()).hexdigest()
    return f"SAPISIDHASH {ts}_{digest}"

def _parse_cookie_blob(raw: str) -> Tuple[str, str]:
    """Любой формат кук → (строка Cookie, SAPISID). Пусто, если распознать не удалось."""
    if not str(raw or "").strip():
        return "", ""
    parsed = parse_cookie_import(raw)
    cookie = parsed.header
    return cookie, _sapisid_from(cookie)

def _sapisid_from(cookie: str) -> str:
    values = parse_header(cookie)
    for name in ("SAPISID", "__Secure-3PAPISID", "__Secure-1PAPISID", "APISID"):
        if values.get(name):
            return values[name]
    return ""

def _set_cookies(resp) -> Dict[str, str]:
    """Set-Cookie ответа Google (только .google.com) → {name: value}."""
    out: Dict[str, str] = {}
    headers = getattr(resp, "headers", None)
    raw: List[str] = []
    if headers is not None:
        getter = getattr(headers, "get_list", None) or getattr(headers, "getall", None) or getattr(headers, "get_all", None)
        if getter:
            try:
                raw = list(getter("set-cookie") or [])
            except Exception:
                raw = []
        if not raw:
            single = headers.get("set-cookie") if hasattr(headers, "get") else None
            raw = [single] if single else []
    for line in raw:
        first, _, attrs = str(line).partition(";")
        if "=" not in first:
            continue
        name, value = (s.strip() for s in first.split("=", 1))
        domain = re.search(r"domain=([^;]+)", attrs, re.I)
        if domain and domain.group(1).strip().lower().lstrip(".") not in ("google.com", "gemini.google.com"):
            continue
        if re.search(r"max-age=0\b|expires=thu, 01 jan 1970", attrs, re.I) or not value:
            continue
        out[name] = value
    return out

class GeminiWebProvider(Provider):
    id = "geminiweb"
    title = "Gemini Web"
    title_en = "Gemini Web"
    icon = "✧"
    description = ("Gemini Web (gemini.google.com) как OpenAI API. Вход — куками Google "
                   "(достаточно __Secure-1PSID и __Secure-1PSIDTS) или кнопкой «Войти через Google».")
    description_en = ("Gemini Web (gemini.google.com) as an OpenAI API. Sign in with Google cookies "
                      "(__Secure-1PSID and __Secure-1PSIDTS are enough) or the Sign in with Google button.")

    def __init__(self) -> None:
        self._sessions: Dict[str, Any] = {}
        self._pages: Dict[str, Tuple[float, proto.PageInfo]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._reqid = random.randint(10000, 99999)
        self._rotator: Optional[asyncio.Task] = None
        self._status: Dict[str, Tuple[float, proto.UserStatus]] = {}
        self._client_ids: Dict[str, str] = {}
        self._limits: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    def account_fields(self) -> List[Field]:
        return [
            Field("cookie", "Куки Google", "textarea", required=False,
                  placeholder="__Secure-1PSID=g.a000…; __Secure-1PSIDTS=sidts-…",
                  help="Вставьте что угодно: строку Cookie из Network, таблицу из Application → Cookies, JSON из "
                       "Cookie-Editor, cookies.txt или только два значения __Secure-1PSID и __Secure-1PSIDTS. "
                       "Пусто — гостевой режим (только Flash, Google может показать капчу).",
                  label_en="Google cookies",
                  help_en="Paste anything: the Cookie header from Network, the Application → Cookies table, a "
                          "Cookie-Editor JSON, cookies.txt, or just the two values __Secure-1PSID and __Secure-1PSIDTS. "
                          "Empty — guest mode (Flash only, Google may show a captcha)."),
            Field("auth_user", "Номер аккаунта Google (/u/N)", "text", required=False,
                  placeholder="0", default="",
                  help="Если в браузере вошли в несколько Google-аккаунтов: цифра из адреса gemini.google.com/u/N/app.",
                  label_en="Google account number (/u/N)",
                  help_en="When several Google accounts are signed in: the digit from gemini.google.com/u/N/app."),
            Field("temporary_chats", "Временные чаты", "select", required=False,
                  default="true", options=["true", "false"],
                  help="true — переписка не сохраняется в истории Gemini.",
                  label_en="Temporary chats",
                  help_en="true — conversations are not stored in your Gemini history."),
            Field("delete_chats", "Удалять чаты прокси из истории", "select", required=False,
                  default="true", options=["true", "false"],
                  help="true — после ответа чат, созданный MemuAI, удаляется из истории Gemini (если Google всё же "
                       "сохранил его, несмотря на режим временного чата).",
                  label_en="Delete proxy chats from history",
                  help_en="true — after the answer the chat created by MemuAI is deleted from your Gemini history "
                          "(in case Google kept it despite the temporary-chat mode)."),
            Field("auto_rotate", "Автообновление входа", "select", required=False,
                  default="true", options=["true", "false"],
                  help="Раз в ~10 минут обновлять __Secure-1PSIDTS и сохранять новые куки в аккаунт.",
                  label_en="Keep the sign-in fresh",
                  help_en="Refresh __Secure-1PSIDTS every ~10 minutes and save the new cookies to the account."),
            Field("gemini_bl", "Версия бэкенда (bl)", "text", required=False,
                  placeholder="boq_gemini-web-uiserver_…",
                  help="Обычно определяется автоматически. Заполните, только если поддержка попросила.",
                  label_en="Backend build (bl)",
                  help_en="Detected automatically. Fill it in only when asked by support."),
        ]

    def validate_fields(self, fields: Dict[str, Any]) -> Optional[str]:
        index = str(fields.get("auth_user", "") or "").strip()
        if index and not index.isdigit():
            return "Номер аккаунта (/u/N) должен быть числом"
        raw = str(fields.get("cookie", "") or "").strip()
        if raw:
            parsed = parse_cookie_import(raw)
            if parsed.errors:
                return parsed.errors[0]["ru"]
            fields["cookie"] = parsed.header
        return None

    def seed_models(self) -> List[Tuple[str, str]]:
        return [(name, cfg["note"]) for name, cfg in MODELS.items()]

    def settings_fields(self) -> List[Field]:
        return [
            Field("default_effort", "Уровень размышлений (Thinking level) по умолчанию", "select",
                  required=False, default="", options=["", "low", "medium", "high"],
                  help="Как контрол «Thinking level» в приложении Gemini: low — быстрее, medium — обычно, high — "
                       "глубокие (расширенные) размышления. Действует, если запрос не задаёт уровень сам "
                       "(reasoning_effort, пикер уровня в чате, суффикс модели @effort=low|medium|high). Пусто — medium.",
                  label_en="Default thinking level",
                  help_en="Like the Thinking level control in the Gemini app: low — faster, medium — normal, high — "
                          "deep (extended) thinking. Used when the request sets no level itself (reasoning_effort, "
                          "the chat level picker, the @effort=low|medium|high model suffix). Empty — medium."),
            Field("media_base_url", "Адрес Proxy для картинок и видео", "text", required=False,
                  placeholder="http://127.0.0.1:8317",
                  help="Картинки и видео, которые нарисовал Gemini, Proxy скачивает куками аккаунта и отдаёт по "
                       "своему адресу …/v1/geminiweb/media/… (ссылки Google без кук не открываются). Пусто — "
                       "http://127.0.0.1:<порт Proxy>. Укажите внешний адрес (туннель, IP в сети), если клиент "
                       "работает на другом устройстве.",
                  label_en="Proxy address for images and videos",
                  help_en="Images and videos made by Gemini are downloaded with the account cookies and served at "
                          "the Proxy's own …/v1/geminiweb/media/… address (Google's links do not open without the "
                          "cookies). Empty — http://127.0.0.1:<Proxy port>. Set an external address (tunnel, LAN IP) "
                          "when the client runs on another device."),
        ]

    def model_note(self, model: str) -> str:
        for status in (self._known_status(a) for a in self._accounts()):
            for item in (status.models if status else []):
                if item.name == model:
                    return f"{item.display} · {item.description}".strip(" ·")[:200]
        return MODELS.get(model, {}).get("note", "")

    async def dynamic_models(self, account) -> Optional[List[str]]:
        """Модели, которые аккаунту реально доступны в выборе модели Gemini; без связи — статический список."""
        status = await self.account_models(account)
        if status and status.models:
            return [m.name for m in status.models if m.available]
        return list(MODELS) if self._cookie(account) else [DEFAULT_MODEL]

    @staticmethod
    def _fields(account) -> Dict[str, Any]:
        return account.fields or {}

    def _cookie(self, account) -> str:
        return _parse_cookie_blob(str(self._fields(account).get("cookie", "") or ""))[0]

    def _prefix(self, account) -> str:
        index = str(self._fields(account).get("auth_user", "") or "").strip()
        return f"/u/{index}" if index.isdigit() else ""

    async def _session(self, account):
        """Отдельная curl-сессия на аккаунт (и цикл событий) — чтобы куки не смешивались."""
        key = f"{getattr(account, 'id', '')}|{self.proxy_for(account)}{http._suffix()}"
        session = self._sessions.get(key)
        if session is None:
            from curl_cffi.requests import AsyncSession
            kwargs: Dict[str, Any] = {"verify": http._cfg["verify"]}
            if http._cfg["impersonate"]:
                kwargs["impersonate"] = http._cfg["impersonate"]
            proxy = self.proxy_for(account)
            if proxy:
                kwargs["proxies"] = {"http": proxy, "https": proxy}
            session = AsyncSession(**kwargs)
            self._sessions[key] = session
        if self._cookie(account):

            try:
                session.cookies.clear()
            except Exception:
                pass
        return session

    def _base_headers(self, account, *, cookie: Optional[str] = None) -> Dict[str, str]:
        headers = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
        prefix = self._prefix(account)
        if prefix:
            headers["X-Goog-AuthUser"] = prefix.rsplit("/", 1)[-1]
        cookie = self._cookie(account) if cookie is None else cookie
        if cookie:
            headers["Cookie"] = cookie
            sapisid = _sapisid_from(cookie)
            if sapisid:
                headers["Authorization"] = _sapisidhash(sapisid)
        return headers

    def _absorb(self, account, resp) -> None:
        """Обновлённые Google куки (Set-Cookie) → поле аккаунта."""
        fresh = _set_cookies(resp)
        current = self._cookie(account)
        if not fresh or not current:
            return
        values = parse_header(current)
        changed = {k: v for k, v in fresh.items() if k in values and values[k] != v}
        if not changed:
            return
        header = current
        for name, value in changed.items():
            header = set_cookie_value(header, name, value)
        self._store_cookie(account, header)

    @staticmethod
    def _store_cookie(account, header: str) -> None:
        account.fields["cookie"] = header
        account.extra["cookie_updated_at"] = time.time()
        try:
            from ..state import state as app_state
            app_state.save_accounts_soon(2.0)
        except Exception:
            pass

    async def rotate_cookies(self, account, *, force: bool = False) -> Optional[str]:
        """POST accounts.google.com/RotateCookies → новый __Secure-1PSIDTS (сохраняется в аккаунт)."""
        cookie = self._cookie(account)
        values = parse_header(cookie)
        if not values.get(PSID):
            return None
        last = float((account.extra or {}).get("rotated_at") or 0)
        if not force and time.time() - last < ROTATE_MIN_GAP:
            return values.get(PSIDTS)
        account.extra["rotated_at"] = time.time()
        minimal = "; ".join(f"{k}={values[k]}" for k in (PSID, PSIDTS) if values.get(k))
        session = await self._session(account)
        resp = await session.post(proto.ROTATE_URL, data='[000,"-0000000000000000000"]', timeout=20,
                                  headers={"Content-Type": "application/json", "Origin": "https://accounts.google.com",
                                           "User-Agent": UA, "Cookie": minimal})
        if resp.status_code == 401:
            raise ProviderAuthError("Google отклонил куки при обновлении входа (401) — войдите заново и обновите куки.")
        if resp.status_code != 200:
            return None
        fresh = _set_cookies(resp)
        new_ts = fresh.get(PSIDTS)
        if new_ts and new_ts != values.get(PSIDTS):
            header = set_cookie_value(cookie, PSIDTS, new_ts)
            for name in ("__Secure-1PSIDCC", "__Secure-3PSIDTS", "__Secure-3PSIDCC"):
                if fresh.get(name) and name in values:
                    header = set_cookie_value(header, name, fresh[name])
            self._store_cookie(account, header)
        return new_ts or values.get(PSIDTS)

    def _ensure_rotator(self) -> None:
        if self._rotator is None or self._rotator.done():
            try:
                self._rotator = spawn_background(self._rotate_loop())
            except RuntimeError:
                self._rotator = None

    async def _rotate_loop(self) -> None:
        from ..state import state as app_state
        while True:
            await asyncio.sleep(90)
            targets = [a for a in list(app_state.accounts.values())
                       if a.provider == self.id and getattr(a, "enabled", True)
                       and str((a.fields or {}).get("auto_rotate", "true")).lower() != "false"
                       and PSID in parse_header(self._cookie(a))]
            if not targets:
                return
            for acc in targets:
                if time.time() - float((acc.extra or {}).get("rotated_at") or 0) < ROTATE_EVERY:
                    continue
                try:
                    await self.rotate_cookies(acc)
                except Exception:
                    continue

    async def page(self, account, *, force: bool = False, cookie: Optional[str] = None) -> proto.PageInfo:
        key = getattr(account, "id", "") or "draft"
        cached = self._pages.get(key)
        if cookie is None and not force and cached and time.time() - cached[0] < PAGE_TTL:
            return cached[1]
        session = await self._session(account)
        headers = self._base_headers(account, cookie=cookie)
        headers["Accept"] = "text/html,application/xhtml+xml"
        resp = await session.get(f"{BASE}{self._prefix(account)}/app", headers=headers, timeout=25,
                                 allow_redirects=True)
        info = proto.parse_page(resp.status_code, str(getattr(resp, "url", "") or ""), resp.text or "")
        override = str(self._fields(account).get("gemini_bl", "") or "").strip()
        if override:
            info.bl = override
        if cookie is None:
            self._absorb(account, resp)
            self._pages[key] = (time.time(), info)
        return info

    @staticmethod
    def _accounts() -> List[Any]:
        try:
            from ..state import state as app_state
            return [a for a in list(app_state.accounts.values()) if a.provider == GeminiWebProvider.id]
        except Exception:
            return []

    def _client_id(self, account) -> str:
        key = getattr(account, "id", "") or "draft"
        if key not in self._client_ids:
            self._client_ids[key] = str(uuid.uuid4()).upper()
        return self._client_ids[key]

    def _known_status(self, account) -> Optional[proto.UserStatus]:
        """Последний известный список моделей аккаунта (память → сохранённый в аккаунте), без сети."""
        key = getattr(account, "id", "") or "draft"
        cached = self._status.get(key)
        if cached:
            return cached[1]
        saved = (getattr(account, "extra", None) or {}).get("gw_models")
        if isinstance(saved, dict) and isinstance(saved.get("models"), list):
            status = proto.UserStatus(status=int(saved.get("status") or 1000), found=True,
                                      tier_flags=list(saved.get("tier_flags") or []),
                                      capability_flags=list(saved.get("capability_flags") or []))
            status.models = [m for m in (proto.AccountModel.from_dict(d) for d in saved["models"]
                                         if isinstance(d, dict)) if m]
            self._status[key] = (float(saved.get("ts") or 0), status)
            return status
        return None

    async def account_models(self, account, *, force: bool = False,
                             info: Optional[proto.PageInfo] = None) -> Optional[proto.UserStatus]:
        """Модели из выбора модели Gemini для этого аккаунта (batchexecute GetUserStatus) с кэшем на 6 часов.

        None — получить не удалось (сеть, формат): тогда работает статический список MODELS."""
        key = getattr(account, "id", "") or "draft"
        known = self._known_status(account)
        cached = self._status.get(key)
        if known and cached and not force and time.time() - cached[0] < MODELS_TTL:
            return known
        try:
            info = info or await self.page(account)
            if not info.bl or info.status != 200:
                return known
            session = await self._session(account)
            prefix = self._prefix(account)
            headers = self._base_headers(account)
            headers.update({"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8", "Origin": BASE,
                            "Referer": f"{BASE}{prefix}/app", "Accept": "*/*", "X-Same-Domain": "1",
                            proto.MODEL_HEADER: proto.batch_model_header(self._client_id(account)),
                            "x-goog-ext-73010989-jspb": "[0]"})
            resp = await session.post(self._batch_url(account, info, proto.RPC_USER_STATUS), headers=headers,
                                      data=proto.encode_batch(proto.RPC_USER_STATUS, "[]", info.at), timeout=20)
            if resp.status_code != 200:
                return known
            self._absorb(account, resp)
            status = proto.parse_user_status(resp.text or "")
        except ProviderError:
            raise
        except Exception:
            return known
        if not status.found:
            return known
        self._status[key] = (time.time(), status)
        if getattr(account, "extra", None) is not None and getattr(account, "id", ""):
            account.extra["gw_models"] = {"ts": time.time(), "status": status.status, "paid": status.paid,
                                          "tier_flags": status.tier_flags, "capability_flags": status.capability_flags,
                                          "models": [m.to_dict() for m in status.models]}
            self._seed_catalog(status)
        return status

    @staticmethod
    def _seed_catalog(status: proto.UserStatus) -> None:
        """Новые модели аккаунта (например, вышла «3.9 Flash») сразу появляются в каталоге Proxy."""
        try:
            from ..state import state as app_state
            app_state.save_accounts_soon(2.0)
            fresh = [(m.name, f"{m.display} · {m.description}".strip(" ·")[:200])
                     for m in status.models if m.available]
            if fresh:
                app_state.seed_provider_models(GeminiWebProvider.id, GeminiWebProvider.id, fresh)
        except Exception:
            pass

    def catalog_hidden(self, model: str) -> bool:
        """Скрыть модель в каталоге: старое имя (3.6/3.7 …) или модель, которой нет ни у одного аккаунта с известным списком."""
        name = str(model or "").lower()
        if name in LEGACY:
            return True
        lists = [s for s in (self._known_status(a) for a in self._accounts()) if s and s.models]
        if not lists:
            return False
        return not any(m.name == name and m.available for s in lists for m in s.models)

    def _resolve_model(self, model: str, account=None, params: Optional[Dict[str, Any]] = None
                       ) -> Tuple[str, Optional[proto.AccountModel], int, int, bool]:
        """Имя модели (+ суффиксы @effort=…/@think=N) → (имя, модель аккаунта | None, MODE, глубина, extended).

        Порядок выбора уровня: суффикс модели → уровень запроса (reasoning_effort / пикер / бюджет Anthropic)
        → настройка «Уровень размышлений по умолчанию» → medium."""
        raw = (model or "").strip()
        think_raw = -1
        level = ""
        for _ in range(2):
            match = _THINK_SUFFIX_RE.search(raw)
            if match:
                think_raw, raw = int(match.group(1)), raw[:match.start()].strip()
            match = _EFFORT_SUFFIX_RE.search(raw)
            if match:
                level, raw = effort_level(match.group(1)), raw[:match.start()].strip()
        name = raw.lower().replace("_", "-") or DEFAULT_MODEL
        legacy_extended = False
        if name in LEGACY:
            name, legacy_extended = LEGACY[name]
        params = params or {}
        if not level:
            requested = params.get("_reasoning_level") or params.get("effort")
            level = effort_level(requested) if str(requested or "") not in ("", "auto") else ""
        if not level and legacy_extended:
            level = "high"
        if not level:
            settings = (getattr(config_store.settings, "provider_settings", None) or {}).get(self.id) or {}
            level = effort_level(settings.get("default_effort"))
            legacy_think = str(settings.get("default_think", "") or "")
            if not level and think_raw < 0 and legacy_think.isdigit():
                think_raw = int(legacy_think)
        think, extended = EFFORTS[level or "medium"]
        if think_raw >= 0:
            think = think_raw

        status = self._known_status(account) if account is not None else None
        picked = self._match(name, status.models) if status and status.models else None
        if picked is not None:
            if not picked.available:
                raise ProviderModelUnavailableError(
                    f"Gemini model «{picked.display}» is available only after signing in to Google.",
                    name, message_ru=f"Модель Gemini «{picked.display}» доступна только со входом в Google "
                                     "(добавьте куки в аккаунт Gemini Web).")
            return picked.name, picked, picked.number, think, extended
        if status and status.models:
            shown = ", ".join(m.display for m in status.models if m.available)
            raise ProviderModelUnavailableError(
                f"Gemini model «{name}» is not available to this Google account. Available: {shown}.",
                name, message_ru=f"Модель «{name}» недоступна этому аккаунту Gemini"
                                 f"{' (нужна подписка Google AI Pro/Ultra)' if _family(name) == 'argon' else ''}. "
                                 f"Доступны: {shown}. Выберите другую модель.")
        cfg = MODELS.get(name) or next((c for n, c in MODELS.items() if c["family"] == _family(name)), None)
        if cfg is None:
            name, cfg = DEFAULT_MODEL, MODELS[DEFAULT_MODEL]
        else:
            name = next(n for n, c in MODELS.items() if c is cfg)
        if not cfg["mode"]:
            raise ProviderModelUnavailableError(
                f"Could not read the internal id of «{cfg['display']}» from gemini.google.com.", name,
                message_ru=f"Не удалось получить с gemini.google.com внутренний id модели «{cfg['display']}» "
                           "(она доступна только с подпиской AI Pro/Ultra). Нажмите «Проверить подключение» "
                           "в аккаунте Gemini Web.")
        return name, None, int(cfg["mode"]), think, extended

    @staticmethod
    def _match(name: str, models: List[proto.AccountModel]) -> Optional[proto.AccountModel]:
        """Имя → модель аккаунта: точное имя, затем то же семейство (flash / lite / pro / argon), новейшая версия."""
        for item in models:
            if item.name == name or item.id == name:
                return item
        family = _family(name)
        same = [m for m in models if _family(m.name) == family or (m.category and _family(m.category) == family)]
        if not same:
            return None

        def version(m: proto.AccountModel) -> float:
            found = re.search(r"\d+(?:\.\d+)?", m.display or m.name)
            return float(found.group(0)) if found else 0.0
        same.sort(key=lambda m: (m.available, version(m)), reverse=True)
        return same[0]

    async def _attachments(self, account, messages: List[Dict[str, Any]], push_id: str) -> List[Tuple[str, str]]:
        """Вложения последнего сообщения (картинки, файлы, видео) → загрузка в Gemini → [(ссылка, имя)] для f.req."""
        try:
            items = await media.load_attachments(messages, media.fetch_public)
        except media.AttachmentError as exc:
            raise ProviderError(f"Gemini Web: {exc}", 400, message_ru=f"Gemini Web: {exc}") from exc
        refs: List[Tuple[str, str]] = []
        for item in items:
            refs.append((await self.upload(account, item.data, item.mime, item.name, push_id), item.name))
        return refs

    async def upload(self, account, data: bytes, mime: str, name: str, push_id: str) -> str:
        """content-push.googleapis.com/upload → путь файла (/contrib_service/…) для f.req.

        Сначала multipart-запрос, как актуальный веб-клиент (HanaokaYuzu/Gemini-API master), при отказе —
        resumable-протокол (start → upload, finalize), как в gemini-web2api."""
        session = await self._session(account)
        push = push_id or proto.DEFAULT_PUSH_ID
        cookie = self._cookie(account)
        common = {"Push-ID": push, "X-Tenant-Id": "bard-storage", "Origin": BASE, "Referer": f"{BASE}/", "User-Agent": UA}
        if cookie:
            common["Cookie"] = cookie
        status = 0
        try:
            from curl_cffi import CurlMime
            part = CurlMime()
            part.addpart(name="file", content_type=mime or "application/octet-stream", filename=name, data=data)
            try:
                resp = await session.post(proto.UPLOAD_URL.rstrip("/"), headers=dict(common), multipart=part,
                                          timeout=180, allow_redirects=True)
            finally:
                part.close()
            status = resp.status_code
            ref = (resp.text or "").strip()
            if status == 200 and ref.startswith("/"):
                return ref
        except ImportError:
            pass
        except Exception:  # noqa: BLE001 — пробуем второй вариант протокола
            status = status or 0
        headers = self._base_headers(account)
        headers.update(common)
        headers.update({"X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                        "X-Goog-Upload-Header-Content-Length": str(len(data)),
                        "X-Goog-Upload-Header-Content-Type": mime,
                        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8"})
        start = await session.post(proto.UPLOAD_URL, data=f"File name: {name}", headers=headers, timeout=30)
        upload_url = start.headers.get("x-goog-upload-url") if start.status_code == 200 else ""
        if not upload_url:
            code = start.status_code or status
            raise ProviderServerError(f"Gemini did not accept the file upload «{name}» (HTTP {code}).", 502,
                                      message_ru=f"Gemini не принял загрузку файла «{name}» (HTTP {code}).")
        done = await session.post(upload_url, data=data, timeout=180,
                                  headers={"X-Goog-Upload-Command": "upload, finalize", "X-Goog-Upload-Offset": "0",
                                           "Content-Type": "application/octet-stream", "User-Agent": UA,
                                           "Push-ID": push, "X-Tenant-Id": "bard-storage"})
        ref = (done.text or "").strip()
        if done.status_code != 200 or not ref.startswith("/"):
            raise ProviderServerError(f"Gemini returned no link for the uploaded file (HTTP {done.status_code}).", 502,
                                      message_ru=f"Gemini не вернул ссылку на загруженный файл (HTTP {done.status_code}).")
        return ref

    def _settings(self) -> Dict[str, Any]:
        return (getattr(config_store.settings, "provider_settings", None) or {}).get(self.id) or {}

    async def _fetch_media(self, account, url: str, *, video: bool = False) -> Optional[Tuple[bytes, str]]:
        """Скачать картинку/видео googleusercontent.com куками аккаунта. Видео, пока рендерится, отвечает 206."""
        session = await self._session(account)
        headers = {"User-Agent": UA, "Referer": f"{BASE}/", "Accept": "*/*"}
        cookie = self._cookie(account)
        if cookie:
            headers["Cookie"] = cookie
        deadline = time.monotonic() + (media.VIDEO_WAIT if video else 0)
        while True:
            try:
                resp = await session.get(url, headers=headers, timeout=120, allow_redirects=True)
            except Exception:  # noqa: BLE001
                return None
            if resp.status_code == 206 and video and time.monotonic() < deadline:
                await asyncio.sleep(media.VIDEO_POLL)
                continue
            if resp.status_code != 200:
                return None
            data = getattr(resp, "content", None)
            if not isinstance(data, (bytes, bytearray)) or not data or len(data) > media.MAX_OUTPUT_BYTES:
                return None
            data = bytes(data)
            kind = media.sniff_mime(data, "")
            if not kind.startswith(("image/", "video/")):
                return None
            return data, kind

    async def _store_remote(self, account, url: str, *, video: bool = False) -> str:
        """Адрес Google → адрес Proxy (…/v1/geminiweb/media/<sha>.<ext>); пусто, если скачать не удалось."""
        candidates = [url] if video else list(dict.fromkeys([media.full_size(url), url]))
        for candidate in candidates:
            got = await self._fetch_media(account, candidate, video=video)
            if got:
                return media.media_url(await asyncio.to_thread(media.store, got[0], got[1]), self._settings())
        return ""

    async def deliver_media(self, account, items: List[media.GeneratedMedia]) -> str:
        """Сгенерированные картинки/видео → markdown со ссылками Proxy (без кук Google ссылки не открываются).
        Не удалось скачать — остаётся исходный адрес Google (откроется в браузере, где вы вошли в Google)."""
        out: List[Tuple[media.GeneratedMedia, str, str]] = []
        for item in items:
            url = await self._store_remote(account, item.url, video=item.kind == "video") or item.url
            thumb = ""
            if item.kind == "video" and item.thumb:
                thumb = await self._store_remote(account, item.thumb) or item.thumb
            out.append((item, url, thumb))
        return media.markdown(out)

    def _gen_headers(self, account, request_uuid: str, picked: Optional[proto.AccountModel],
                     extended: bool) -> Dict[str, str]:
        headers = self._base_headers(account)
        headers.update({
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": BASE, "Referer": f"{BASE}{self._prefix(account)}/app", "Accept": "*/*",
            "X-Same-Domain": "1", "x-goog-ext-525005358-jspb": f'["{request_uuid}",1]',
        })
        if picked is not None:

            headers[proto.MODEL_HEADER] = picked.header(self._client_id(account), extended=extended)
            headers["x-goog-ext-73010989-jspb"] = "[0]"
            headers["x-goog-ext-73010990-jspb"] = "[0,0,0]"
        return headers

    def _url(self, account, info: proto.PageInfo) -> str:
        self._reqid += 100000
        params = {"bl": info.bl, "f.sid": info.fsid, "hl": "en", "_reqid": str(self._reqid % 10_000_000), "rt": "c"}
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v})
        return f"{BASE}{self._prefix(account)}{proto.RPC_PATH}?{query}"

    def _batch_url(self, account, info: proto.PageInfo, rpcid: str, source_path: str = "/app") -> str:
        self._reqid += 100000
        prefix = self._prefix(account)
        params = {"rpcids": rpcid, "source-path": f"{prefix}{source_path}", "bl": info.bl, "f.sid": info.fsid, "hl": "en",
                  "_reqid": str(self._reqid % 10_000_000), "rt": "c"}
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v})
        return f"{BASE}{prefix}{proto.BATCH_PATH}?{query}"

    async def _batch(self, account, info: proto.PageInfo, rpcid: str, payload: str = "[]",
                     source_path: str = "/app") -> str:
        """Один вызов batchexecute → текст ответа ("" при любой ошибке: лимиты — необязательная информация)."""
        try:
            session = await self._session(account)
            headers = self._base_headers(account)
            headers.update({"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8", "Origin": BASE,
                            "Referer": f"{BASE}{self._prefix(account)}{source_path}", "Accept": "*/*",
                            "X-Same-Domain": "1",
                            proto.MODEL_HEADER: proto.batch_model_header(self._client_id(account)),
                            "x-goog-ext-73010989-jspb": "[0]"})
            resp = await session.post(self._batch_url(account, info, rpcid, source_path), headers=headers,
                                      data=proto.encode_batch(rpcid, payload, info.at), timeout=20)
            if resp.status_code != 200:
                return ""
            self._absorb(account, resp)
            return resp.text or ""
        except Exception:  # noqa: BLE001
            return ""

    async def account_limits(self, account, *, force: bool = False,
                             info: Optional[proto.PageInfo] = None) -> Dict[str, Any]:
        """Лимиты из окна «Лимиты использования» Gemini (GetUsageInfo + CheckGeminiQuota + CheckQuota), кэш 2 мин.

        Только для вошедшего аккаунта; формат — как у карточки «Лимиты» Proxy (rows/subscription/next_reset)."""
        key = getattr(account, "id", "") or "draft"
        cached = self._limits.get(key)
        if cached and not force and time.time() - cached[0] < LIMITS_TTL:
            return cached[1]
        if not self._cookie(account):
            return {}
        info = info or await self.page(account)
        if not info.signed_in or not info.bl:
            return {}
        usage = limits.parse_usage_info(await self._batch(account, info, limits.RPC_USAGE_INFO, "[]", "/usage"))
        quotas: List[Dict[str, Any]] = []
        for payload, _group in limits.QUOTA_PAYLOADS:
            quotas += limits.parse_gemini_quota(await self._batch(account, info, limits.RPC_GEMINI_QUOTA, payload))
        extra = limits.parse_extra_quota(await self._batch(account, info, limits.RPC_EXTRA_QUOTA, "[]"))
        status = self._known_status(account)
        result = limits.summary(usage, quotas, extra, paid=status.paid if status else None)
        if info.email:
            result["email"] = info.email
        result["ts"] = time.time()
        self._limits[key] = (time.time(), result)
        if getattr(account, "extra", None) is not None and getattr(account, "id", ""):
            account.extra["gw_limits"] = result
        return result

    async def usage(self, account) -> Optional[Dict[str, Any]]:
        """Карточка аккаунта → «Лимиты»: тариф, окна 5 ч / неделя, счётчики Pro/Flash/Thinking, доп. функции."""
        if not self._cookie(account):
            return {"rows": [], "notice": "Гостевой режим: лимиты есть только у вошедшего аккаунта Google.",
                    "notice_en": "Guest mode: limits exist only for a signed-in Google account."}
        return await self.account_limits(account, force=True)

    async def delete_chat(self, account, cid: str) -> bool:
        """DeleteConversation: чат, созданный прокси, не остаётся в истории Gemini пользователя."""
        if not cid or not self._cookie(account):
            return False
        try:
            info = await self.page(account)
            session = await self._session(account)
            headers = self._base_headers(account)
            headers.update({"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8", "Origin": BASE,
                            "Referer": f"{BASE}{self._prefix(account)}/app", "X-Same-Domain": "1",
                            proto.MODEL_HEADER: proto.batch_model_header(self._client_id(account)),
                            "x-goog-ext-73010989-jspb": "[0]"})
            resp = await session.post(self._batch_url(account, info, proto.RPC_DELETE_CHAT), headers=headers,
                                      data=proto.encode_batch(proto.RPC_DELETE_CHAT, json.dumps([cid]), info.at),
                                      timeout=15)
            return resp.status_code == 200
        except Exception:
            return False

    async def chat_stream(self, account, model: str, messages: List[Dict[str, Any]],
                          params: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
        system_text, history, final_user = normalize_messages(messages)
        prompt = transcript_prompt(system_text, history, final_user)
        fields = self._fields(account)
        temporary = str(fields.get("temporary_chats", "true")).lower() != "false"
        delete_after = str(fields.get("delete_chats", "true")).lower() != "false"
        has_cookie = bool(self._cookie(account))
        if has_cookie and str(fields.get("auto_rotate", "true")).lower() != "false":
            self._ensure_rotator()
        try:
            info = await self.page(account)
        except Exception as exc:
            raise ProviderNetworkError(f"gemini.google.com недоступен: {http.network_error_text(exc)}") from exc
        self._raise_page_problem(info, has_cookie)
        status = await self.account_models(account, info=info)
        self._raise_account_status(status, has_cookie)
        account.extra.pop("no_model_header", None)
        name, picked, mode, think, extended = self._resolve_model(model, account, params)

        file_refs = await self._attachments(account, messages, info.push_id)

        attempt = 0
        cids: List[str] = []
        try:
            while True:
                attempt += 1
                request_uuid = str(uuid.uuid4()).upper()
                inner = proto.build_freq(prompt, mode, think, temporary=temporary, extended=extended,
                                         file_refs=file_refs, request_uuid=request_uuid)
                session = await self._session(account)
                try:
                    resp = await session.post(self._url(account, info), data=proto.encode_body(inner, info.at),
                                              headers=self._gen_headers(account, request_uuid, picked, extended),
                                              stream=True, timeout=config_store.settings.request_timeout)
                except Exception as exc:
                    raise ProviderNetworkError(f"Gemini Web is unavailable: {http.network_error_text(exc)}") from exc
                code = resp.status_code
                if code != 200:
                    body = await http.read_error_text(resp, 4000)
                    await http.close_response(resp)
                    if code in (400, 401) and attempt == 1:
                        info = await self.page(account, force=True)
                        self._raise_page_problem(info, has_cookie)
                        continue
                    self._raise_http(code, body, has_cookie)
                self._absorb(account, resp)
                retry: Optional[_Retry] = None
                try:
                    async for event in self._read_stream(resp, hold=not has_cookie, cids=cids, account=account):
                        yield event
                    return
                except _Retry as signal:
                    retry = signal
                finally:
                    await http.close_response(resp)
                if attempt >= 3:
                    if retry.canned:
                        raise self._canned_error(name, picked, retry.message)
                    raise ProviderServerError(retry.message, 502)
                if retry.model_problem:

                    if extended:
                        extended = False
                    else:
                        if retry.canned and attempt >= 2:
                            raise self._canned_error(name, picked, retry.message)
                        status = await self.account_models(account, force=True, info=info)
                        self._raise_account_status(status, has_cookie)
                        name, picked, mode, think, _ext = self._resolve_model(model, account, params)
                await asyncio.sleep(1.5 if attempt == 1 else 3)
        finally:
            if delete_after and cids:
                for cid in dict.fromkeys(cids):
                    spawn_background(self.delete_chat(account, cid))

    @staticmethod
    def _canned_error(name: str, picked: Optional[proto.AccountModel], text: str) -> ProviderError:
        label = picked.display if picked else name
        quote = " ".join((text or "").split())[:120]
        return ProviderModelUnavailableError(
            f"Gemini answered with a service stub instead of a reply («{quote}»): model «{label}» is unavailable to "
            "this account or Google did not accept the request. Pick another model; Proxy → Accounts → Gemini Web → "
            "Check connection lists the account's models.", name,
            message_ru=f"Gemini прислал служебную заглушку вместо ответа («{quote}»): модель «{label}» недоступна "
                       "этому аккаунту или Google не принял запрос. Выберите другую модель — список моделей "
                       "аккаунта: Proxy → Аккаунты → Gemini Web → «Проверить подключение».")

    async def _read_stream(self, resp, *, hold: bool = False, cids: Optional[List[str]] = None,
                           account=None) -> AsyncIterator[Dict[str, Any]]:
        """hold=True (гость): ответ копится до конца — Google шлёт гостю заглушку «I'm having a hard time…»
        и только потом код 1096; так ошибка доходит до пула раньше текста и он пробует другой аккаунт.
        Вошедшему начало ответа придерживается, пока текст короче proto.CANNED_MAX_LEN: короткая служебная
        заглушка («Я всего лишь языковая модель») — признак неверного режима, это ошибка, а не ответ."""
        held: List[Dict[str, Any]] = []
        parser = proto.FrameParser()
        sent_text = sent_thoughts = ""
        images: List[str] = []
        generated: Dict[str, media.GeneratedMedia] = {}
        saw_payload = False
        released = False
        cids = cids if cids is not None else []
        idle = config_store.settings.stream_idle_timeout

        def take(snap: proto.Snapshot) -> None:
            nonlocal sent_text, sent_thoughts, saw_payload
            if snap.cid and snap.cid not in cids:
                cids.append(snap.cid)
            for item in snap.media:
                generated.setdefault(item.url, item)
            if not (snap.text or snap.thoughts or snap.images or snap.media):
                return
            saw_payload = True
            piece, sent_thoughts = proto.delta(snap.thoughts, sent_thoughts)
            if piece:
                held.append({"type": "reasoning", "text": piece})
            piece, sent_text = proto.delta(proto.clean_text(snap.text), sent_text)
            if piece:
                held.append({"type": "text", "text": piece})

        try:
            async for line in http.aiter_lines_str(resp, idle_timeout=idle):
                frames = parser.feed(line + "\n")
                if "BardErrorInfo" in line:
                    self._raise_bard(line)
                events = list(self._events(frames, images))

                for event in events:
                    if event[0] == "error":
                        self._raise_code(event[1], "" if not released else sent_text, self._known_reset(account))
                for event in events:
                    take(event[1])
                if not hold and (released or len(sent_text) > proto.CANNED_MAX_LEN):
                    released = True
                    for item in held:
                        yield item
                    held.clear()
            for event in self._events(parser.flush(), images):
                if event[0] == "error":
                    self._raise_code(event[1], "" if not released else sent_text, self._known_reset(account))
                take(event[1])
        except (ProviderError, _Retry):
            raise
        except asyncio.TimeoutError as exc:
            raise ProviderNetworkError(f"Gemini sent no data for over {idle}s") from exc
        except Exception as exc:
            raise ProviderServerError(f"Обрыв потока Gemini: {type(exc).__name__}: {exc}") from exc
        if not released and not images and not generated and limits.is_limit_reply(sent_text):
            raise self._limit_error(sent_text, account)
        if not released and not images and not generated and proto.is_canned_reply(sent_text):
            raise _Retry(sent_text, model_problem=True, canned=True)
        for item in held:
            yield item
        if images:
            links = "\n".join(f"![Gemini image {i + 1}]({url})" for i, url in enumerate(dict.fromkeys(images)))
            yield {"type": "text", "text": ("\n\n" if sent_text else "") + links}
            sent_text += links
        if generated:
            items = list(generated.values())
            if any(item.kind == "video" for item in items):
                yield {"type": "reasoning", "text": "\nGemini is rendering the video (Veo)…"}
            links = await self.deliver_media(account, items) if account is not None else \
                media.markdown([(item, item.url, item.thumb) for item in items])
            yield {"type": "text", "text": ("\n\n" if sent_text else "") + links}
            sent_text += links
        if not sent_text.strip():
            if saw_payload:
                raise ProviderContentFilterError(
                    "Gemini вернул пустой ответ — обычно это отказ по фильтрам безопасности.")
            raise ProviderServerError("Gemini Web не прислал текст. Проверьте, открывается ли gemini.google.com "
                                      "(в некоторых регионах нужен прокси).", 502)

    @staticmethod
    def _events(frames: List[Any], images: List[str]):
        for frame in frames:
            for part in proto.iter_parts(frame):
                code = proto.error_code(part)
                if code:
                    yield ("error", code)
                    continue
                snap = proto.parse_part(part)
                if snap is None:
                    continue
                for url in snap.images:
                    if url not in images:
                        images.append(url)
                yield ("snap", snap)

    @staticmethod
    def _raise_page_problem(info: proto.PageInfo, has_cookie: bool) -> None:
        if info.captcha or info.status == 429:
            raise ProviderRateLimitError(
                "Google показал капчу «необычный трафик» для этого IP. Войдите куками Google, "
                "смените сеть или задайте прокси в аккаунте.")
        if has_cookie and info.login_page:
            raise ProviderAuthError("Куки просрочены — Google вернул страницу входа. Обновите куки "
                                    "(или нажмите «Войти через Google»).")
        if has_cookie and info.status == 200 and not info.signed_in:
            raise ProviderAuthError("Google не узнал аккаунт по кукам (нет токена SNlM0e). Скопируйте куки заново "
                                    "из gemini.google.com, проверьте номер аккаунта /u/N.")
        if info.status and info.status != 200:
            raise ProviderServerError(f"gemini.google.com ответил HTTP {info.status}", info.status)

    @staticmethod
    def _raise_account_status(status: Optional[proto.UserStatus], has_cookie: bool) -> None:
        if not status or status.status in (0, 1000):
            return
        ru, en = proto.ACCOUNT_STATUS.get(status.status, (f"Статус аккаунта Gemini {status.status}.",
                                                          f"Gemini account status {status.status}."))
        if status.status == 1016 and has_cookie:
            raise ProviderAuthError(f"{en} {ru} Обновите куки (или нажмите «Войти через Google»).")
        if status.status in proto.ACCOUNT_BLOCKED:
            raise ProviderServerError(f"{en} ({status.status})", 403, message_ru=f"{ru} ({status.status})")

    @staticmethod
    def _raise_http(status: int, body: str, has_cookie: bool) -> None:
        if status == 429 or "g-recaptcha" in (body or ""):
            raise ProviderRateLimitError("Google ограничил запросы (HTTP 429 / капча). "
                                         + ("Подождите или смените IP." if has_cookie else
                                            "Гостевой режим с этого IP заблокирован — добавьте куки Google."))
        if status in (401, 403):
            raise ProviderAuthError(f"Google отклонил куки Gemini (HTTP {status}). Обновите куки в аккаунте.")
        raise ProviderServerError(f"Gemini Web: HTTP {status}", status)

    def _known_reset(self, account) -> Optional[float]:
        """Ближайший сброс лимита из последних прочитанных счётчиков аккаунта (без сети)."""
        if account is None:
            return None
        cached = self._limits.get(getattr(account, "id", "") or "draft")
        saved = cached[1] if cached else (getattr(account, "extra", None) or {}).get("gw_limits")
        return limits.soonest_reset(saved if isinstance(saved, dict) else None)

    def _limit_error(self, text: str, account) -> ProviderRateLimitError:
        """Ответ «вы достигли лимита …» вместо ответа → понятная 429 со временем сброса (если известно)."""
        reset = limits.reset_from_text(text) or self._known_reset(account)
        quote = " ".join((text or "").split())[:200]
        return self._rate_limit(f"Gemini usage limit reached: «{quote}».",
                                f"Gemini сообщил об исчерпании лимита: «{quote}».", reset)

    @staticmethod
    def _rate_limit(en: str, ru: str, reset: Optional[float]) -> ProviderRateLimitError:
        when_en = when_ru = ""
        wait = None
        if reset and reset > time.time():
            stamp = time.strftime("%d.%m %H:%M", time.localtime(reset))
            when_en, when_ru = f" Resets at {stamp}.", f" Сброс в {stamp}."
            wait = max(1.0, reset - time.time())
        err = ProviderRateLimitError(f"{en}{when_en} {ru}{when_ru} Возьмите другую модель или подождите сброса.",
                                     retry_after=wait)
        err.message_ru = f"{ru}{when_ru} Возьмите другую модель или подождите сброса."
        return err

    @classmethod
    def _raise_code(cls, code: int, sent: str, reset: Optional[float] = None) -> None:
        ru, en = proto.ERROR_TEXTS.get(code, (f"Gemini вернул ошибку {code}.", f"Gemini returned error {code}."))
        if code in (1050, 1052) and not sent:
            raise _Retry(f"{en} {ru}", model_problem=True)
        if code == 1013 and not sent:
            raise _Retry(en)
        if code == 1037:
            raise cls._rate_limit(en, ru, reset)
        if code == 1096:
            raise ProviderRateLimitError(f"{en} {ru}")
        raise ProviderServerError(f"{en} {ru}", 502)

    @staticmethod
    def _raise_bard(blob: str) -> None:
        match = proto.BARD_ERROR_RE.search(blob)
        if match:
            code = int(match.group(1))
            if code in (1037, 1052):
                raise ProviderContentFilterError(f"Gemini refused to answer this request (BardErrorInfo {code}).")
            raise ProviderServerError(f"Gemini rejected the request (BardErrorInfo {code})", 502)

    async def diagnose(self, account, *, test_request: bool = True) -> Dict[str, Any]:
        """Пошаговая проверка: куки → страница → токен → тестовый запрос → модели."""
        from .geminiweb_diag import run_diagnosis
        return await run_diagnosis(self, account, test_request=test_request)

    async def check(self, account) -> Tuple[Optional[bool], str]:
        result = await self.diagnose(account, test_request=False)
        return result["ok"], result["summary"]

    async def close(self) -> None:
        if self._rotator is not None:
            self._rotator.cancel()
        sessions, self._sessions = list(self._sessions.values()), {}
        for session in sessions:
            await http.close_quietly(session.close)

class _Retry(Exception):
    def __init__(self, message: str, *, model_problem: bool = False, canned: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.model_problem = model_problem
        self.canned = canned
