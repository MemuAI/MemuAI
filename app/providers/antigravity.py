"""Провайдер Google Antigravity: OAuth Google → cloudcode-pa (Gemini/Claude/GPT-OSS).

Вход повторяет флоу Antigravity IDE: локальный колбэк на 127.0.0.1:51121,
Google OAuth с клиентом Antigravity, loadCodeAssist → projectId, чат через
v1internal:streamGenerateContent?alt=sse.

Имена моделей (уровни размышлений Gemini, «-thinking» у Claude) — в
antigravity_models.py, разбор лимитов — в antigravity_quota.py.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
import time
import uuid
import weakref
import webbrowser
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse, parse_qs

from .base import (
    Field,
    Provider,
    ProviderAuthError,
    ProviderContentFilterError,
    ProviderError,
    ProviderModelUnavailableError,
    ProviderNetworkError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderServerError,
    content_to_text,
    spawn_background,
)
from ..config import config_store
from .. import oauth_config
from . import antigravity_models as models
from . import antigravity_quota as quota
from . import http

def _oauth_client() -> Tuple[str, str]:
    client_id, client_secret, _ = oauth_config.client("antigravity")
    if not client_id or not client_secret:
        raise ProviderAuthError(
            "OAuth-клиент Antigravity не настроен. Задайте MEMUAI_ANTIGRAVITY_CLIENT_ID "
            "и MEMUAI_ANTIGRAVITY_CLIENT_SECRET перед запуском или настройте приватную сборку "
            "через oauth_clients.local.json (см. OAUTH_SETUP.md).")
    return client_id, client_secret

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = "https://www.googleapis.com/auth/cloud-platform https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/userinfo.profile"

ENDPOINTS = [
    "https://cloudcode-pa.googleapis.com",
    "https://daily-cloudcode-pa.googleapis.com",
]
PROD_ENDPOINT = ENDPOINTS[0]

FALLBACK_VERSION = "2.0.6"
VERSION_URL = ("https://antigravity-auto-updater-974169037036.us-central1.run.app"
               "/api/update/linux-x64/stable/latest")
VERSION_TTL = 6 * 3600
VERSION_CHECK = True
_client_version: Dict[str, Any] = {"value": FALLBACK_VERSION, "checked": 0.0}

_VERSION_RE = re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,4}")

def pinned_version() -> str:
    """Версия, которую пользователь задал вручную в настройках провайдера («» — определять самим)."""
    value = str(config_store.provider_cfg("antigravity").get("client_version") or "").strip()
    return value if _VERSION_RE.fullmatch(value) else ""

def client_version() -> str:
    return pinned_version() or str(_client_version["value"])

def user_agent() -> str:
    return f"antigravity/{client_version()} win32/x64"

UA = user_agent()

SEED = models.seed_rows()

DEPRECATED = models.DEPRECATED
_deprecated_learned = models._learned_deprecated
_models_checked_at: Dict[str, float] = {}
MODEL_CATALOG_TTL = 5 * 60

_quota_hits: Dict[str, Dict[str, float]] = {}
WEEKLY_WINDOW = quota.WEEK
_iso_ts = quota.iso_ts

ENDPOINT_PENALTY = 180.0
_endpoint_bad_until: Dict[str, float] = {}
_last_good_endpoint: List[str] = []

MAX_INLINE_WAIT = 8.0
INLINE_WAIT_PAD = 0.25

SAMPLE_EVERY = 600.0
SAMPLE_AFTER_CHAT = True
_last_sample: Dict[str, float] = {}
_tier_cache: Dict[str, Tuple[float, str]] = {}

_reduced_safety: Dict[str, str] = {}

EFFORT_BUDGETS = {"off": 0, "minimal": 0, "low": 2048, "medium": 8192, "high": 16384, "xhigh": 24576, "max": 32000}
DEFAULT_THINKING_BUDGET = 8192

SAFETY = [{"category": f"HARM_CATEGORY_{c}", "threshold": "OFF"}
          for c in ("HARASSMENT", "HATE_SPEECH", "SEXUALLY_EXPLICIT", "DANGEROUS_CONTENT", "CIVIC_INTEGRITY")]

_REFUSAL_FINISH = frozenset({"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY"})

def reset_health() -> None:
    """Забыть, какие хосты и когда подводили (для тестов и «Перезапустить службы»)."""
    _endpoint_bad_until.clear()
    _last_good_endpoint.clear()
    _last_sample.clear()
    _tier_cache.clear()
    _reduced_safety.clear()
    _client_version.update(value=FALLBACK_VERSION, checked=0.0)

def ordered_endpoints(choice: str = "auto") -> List[str]:
    """Хосты в порядке попыток: сначала не подводившие, среди них — последний удачный."""
    if choice == "prod":
        return [PROD_ENDPOINT]
    if choice == "daily":
        return [ENDPOINTS[1]]
    now = time.time()
    good = _last_good_endpoint[0] if _last_good_endpoint else ""
    return sorted(ENDPOINTS, key=lambda host: (_endpoint_bad_until.get(host, 0.0) > now,
                                               host != good, ENDPOINTS.index(host)))

def _endpoint_failed(host: str) -> None:
    _endpoint_bad_until[host] = time.time() + ENDPOINT_PENALTY

def _endpoint_worked(host: str) -> None:
    _endpoint_bad_until.pop(host, None)
    _last_good_endpoint[:] = [host]

def quota_pool(model: str) -> str:
    low = str(model or "").lower()
    return "Gemini" if low.startswith("gemini") else "Claude / GPT"

def current_model_id(model: str) -> str:
    """The id to send for a public name, a retired id or an id Google already knows."""
    return models.upstream_id(model)

_is_internal_model = models.is_internal

@dataclass
class GoogleError:
    message: str = ""
    status: str = ""
    reason: str = ""
    retry_after: float = 0.0
    url: str = ""

_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|h|m|s)")
_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}

def _seconds(text: Any) -> float:
    return sum(float(value) * _UNITS[unit] for value, unit in _DURATION.findall(str(text or "")))

def parse_google_error(body: str) -> GoogleError:
    """``{"error": {message, status, details: [...]}}`` → что из этого нужно."""
    text = str(body or "").strip()
    info = GoogleError()
    try:
        data = json.loads(text)
    except ValueError:
        info.message = re.sub(r"\s+", " ", text)[:300]
        return info
    if isinstance(data, list) and data:
        data = data[0]
    error = data.get("error") if isinstance(data, dict) else None
    if not isinstance(error, dict):
        info.message = re.sub(r"\s+", " ", text)[:300]
        return info
    info.message = str(error.get("message") or "")[:400]
    info.status = str(error.get("status") or "")
    for detail in error.get("details") or []:
        if not isinstance(detail, dict):
            continue
        if detail.get("reason"):
            info.reason = str(detail["reason"])
        meta = detail.get("metadata") if isinstance(detail.get("metadata"), dict) else {}
        delay = detail.get("retryDelay") or meta.get("quotaResetDelay") or meta.get("retryDelay")
        if delay and not info.retry_after:
            info.retry_after = _seconds(delay)
        if meta.get("validation_url") and not info.url:
            info.url = str(meta["validation_url"])
    if not info.retry_after:
        match = re.search(r"reset after ([0-9hms.]+)", info.message, re.I)
        if match:
            info.retry_after = _seconds(match.group(1))
    return info

def _error_message(body: str) -> str:
    """Google's own error text: ``{"error": {"message": ...}}`` or the raw head."""
    info = parse_google_error(body)
    if info.message:
        status = f" [{info.status}]" if info.status else ""
        return f"{info.message}{status}"
    return re.sub(r"\s+", " ", str(body or "")).strip()[:300]

def _location_blocked(info: GoogleError) -> bool:
    return "location is not supported" in info.message.lower()

def _verification_message(info: GoogleError) -> str:
    return ("Google asks to verify this account (“Verify your account”). Open "
            "antigravity.google or sign in to the account in a browser, pass the "
            "check, then press “Check” on the account card. "
            "Google просит подтвердить аккаунт: пройдите проверку в браузере и нажмите «Проверить».")

_refresh_locks: "weakref.WeakKeyDictionary[Any, Dict[str, asyncio.Lock]]" = weakref.WeakKeyDictionary()

def _refresh_lock(account) -> asyncio.Lock:
    """Один обмен refresh-токена на аккаунт: параллельные запросы не обновляют его вразнобой."""
    loop = asyncio.get_running_loop()
    locks = _refresh_locks.setdefault(loop, {})
    return locks.setdefault(str(getattr(account, "id", "")), asyncio.Lock())

class AntigravityProvider(Provider):
    id = "antigravity"
    title = "Antigravity"
    icon = "▲"
    oauth = True
    oauth_port = 51121

    native_search = True
    title_en = "Antigravity"
    description = "Google Antigravity: модели и их доступность определяются сервером для вашего аккаунта. Вход через Google."
    description_en = "Google Antigravity: models and availability come from your account's server catalogue. Sign in with Google."

    def __init__(self) -> None:
        self._flows: Dict[str, Dict[str, Any]] = {}
        self._display: Dict[str, str] = {}
        self._notices: Dict[str, Dict[str, str]] = {}
        self._tasks: set = set()
        self._catalog_data: Dict[str, Dict[str, Any]] = {}
        self._catalog_errors: Dict[str, str] = {}

    def account_fields(self) -> List[Field]:
        return [
            Field("access_token", "Access token", "password", required=False,
                  help="Можно вставить вручную, либо нажмите «Войти через браузер»",
                  help_en="Paste it manually or press “Sign in via browser”"),
            Field("refresh_token", "Refresh token", "password", required=False),
            Field("project_id", "Project ID", "text", required=False,
                  help="Определится автоматически при первом запросе",
                  help_en="Detected automatically on the first request"),
        ]

    def seed_models(self) -> List[Tuple[str, str]]:

        return []

    def model_note(self, model: str) -> str:
        return self._display.get(model) or models.note_for(model)

    def model_availability(self, model: str, accounts=None) -> dict:
        """Aggregate an account-scoped snapshot without losing the user's enabled switch."""
        if accounts is None:
            from ..state import state
            accounts = state.usable_accounts(self.id)
        availability = []
        checked = []
        for account in accounts:
            snapshot = (getattr(account, "extra", {}) or {}).get("antigravity_model_catalog") or {}
            rows = snapshot.get("models")
            if not isinstance(rows, dict) or not snapshot.get("checked"):
                availability.append(None)
                continue
            checked.append(float(snapshot["checked"]))
            row = rows.get(model)
            if row is None:
                raw = models.upstream_id(model)
                row = next((value for value in rows.values() if value.get("upstream") == raw), None)
            availability.append(bool(row and row.get("available")))
        if not availability or not checked:
            return {}
        available = True if True in availability else (None if None in availability else False)
        reason = "" if available is not False else "Недоступна для подключённых аккаунтов или текущего клиента Antigravity"
        return {"available": available, "reason": reason, "checked": max(checked), "source": "api:antigravity"}

    def account_model_available(self, account, model: str) -> bool:
        return self.model_availability(model, [account]).get("available") is not False

    @staticmethod
    def _request_model(account, requested: str) -> str:
        snapshot = (getattr(account, "extra", {}) or {}).get("antigravity_model_catalog") or {}
        rows = snapshot.get("models") or {}
        row = rows.get(requested) if isinstance(rows, dict) else None
        if isinstance(row, dict) and isinstance(row.get("upstream"), str) and row["upstream"]:

            models.remember_model(requested, row["upstream"], str(row.get("display") or ""))
            return row["upstream"]
        return current_model_id(requested)

    def settings_fields(self) -> List[Field]:
        return [
            Field("max_output_tokens", "Максимум токенов ответа", "number", required=False,
                  default="64000",
                  label_en="Max output tokens"),
            Field("thinking_budget", "Бюджет размышлений Claude (токены, 0 — выключить)", "number",
                  required=False, default=str(DEFAULT_THINKING_BUDGET),
                  help="Модели «Claude … (Thinking)» думают только по явному бюджету. "
                       "Глубина рассуждений клиента (/effort) меняет его: low 2048, medium 8192, high 16384, max 32000.",
                  label_en="Claude thinking budget (tokens, 0 turns it off)",
                  help_en="“Claude … (Thinking)” models think only with an explicit budget. The client's "
                          "effort level changes it: low 2048, medium 8192, high 16384, max 32000."),
            Field("show_thoughts", "Показывать размышления Gemini", "select", required=False,
                  default="on", options=["on", "off"],
                  help="on — краткое изложение хода мыслей приходит как reasoning_content; "
                       "уровень размышлений Gemini выбирается моделью: -low, -medium, -high "
                       "(у Gemini 3.7 / 3.8 Flash без уровня в имени — /effort клиента).",
                  label_en="Show Gemini thoughts",
                  help_en="on — a summary of the thinking arrives as reasoning_content; Gemini's "
                          "thinking level is chosen by the model: -low, -medium, -high."),
            Field("client_version", "Версия клиента Antigravity (пусто — определять автоматически)", "text",
                  required=False, default="",
                  help="Обычно не нужна: Proxy сверяется с адресом автообновления Antigravity. "
                       "Если задать вручную (например, 2.0.6), сверка отключается.",
                  label_en="Antigravity client version (empty — detect automatically)",
                  help_en="Normally not needed: the Proxy checks Antigravity's own update address. "
                          "A value set by hand (for example 2.0.6) turns that check off."),
            Field("safety", "Фильтры безопасности", "select", required=False,
                  default="off", options=["off", "default"],
                  help="off — как в Antigravity IDE; default — настройки Google.",
                  label_en="Safety filters",
                  help_en="off matches the Antigravity IDE; default uses Google's own."),
            Field("endpoint", "Эндпоинт", "select", required=False, default="auto",
                  options=["auto", "prod", "daily"],
                  help="auto пробует оба адреса и первым берёт тот, что отвечал последним.",
                  label_en="Endpoint",
                  help_en="auto tries both hosts, the one that answered last first."),
        ]

    @staticmethod
    def _stored_tokens(account) -> Dict[str, Any]:
        extra = getattr(account, "extra", None) or {}
        fields = getattr(account, "fields", None) or {}
        return extra.get("oauth") or {
            "access_token": fields.get("access_token", ""),
            "refresh_token": fields.get("refresh_token", ""),
            "expires_at": extra.get("expires_at", 0),
        }

    async def _ensure_token(self, account, *, force: bool = False, stale: str = "") -> str:
        """Действующий access token; ``force`` — обновить, даже если срок не вышел.

        ``stale`` — токен, который Google только что отверг: если другой запрос
        уже обновил его, берём новый, а не обновляем ещё раз.
        """
        def fresh(tok: Dict[str, Any]) -> bool:
            return bool(tok.get("access_token") and tok.get("expires_at", 0) > time.time() + 300)

        tok = self._stored_tokens(account)
        if not force and fresh(tok):
            return tok["access_token"]
        async with _refresh_lock(account):
            tok = self._stored_tokens(account)
            if not force and fresh(tok):
                return tok["access_token"]
            if force and stale and tok.get("access_token") and tok["access_token"] != stale and fresh(tok):
                return tok["access_token"]
            if not tok.get("refresh_token"):
                raise ProviderAuthError("No Antigravity refresh token — sign in again")
            client_id, client_secret = _oauth_client()
            try:
                session = await http.get_session(self.proxy_for(account))
                resp = await session.post(TOKEN_URL, data={
                    "client_id": client_id, "client_secret": client_secret,
                    "refresh_token": tok["refresh_token"], "grant_type": "refresh_token",
                }, timeout=30)
            except Exception as e:
                raise ProviderNetworkError(f"Token refresh failed: {e}")
            if resp.status_code in (400, 401, 403):
                raise ProviderAuthError(f"Google rejected the refresh (HTTP {resp.status_code})")
            if resp.status_code != 200:

                raise ProviderServerError(f"Google token refresh failed (HTTP {resp.status_code})", 502)
            try:
                data = resp.json()
            except Exception:
                data = None
            if not isinstance(data, dict) or not data.get("access_token"):

                raise ProviderServerError("Google token refresh returned no access token", 502)
            tok = {"access_token": data["access_token"],

                   "refresh_token": data.get("refresh_token") or tok["refresh_token"],
                   "expires_at": time.time() + int(data.get("expires_in") or 3600) - 300}
            account.extra["oauth"] = tok
            account.fields["access_token"] = tok["access_token"][:4] + "…"
            from ..state import state as app_state
            app_state.save_accounts()
            return tok["access_token"]

    def _api_headers(self, token: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": user_agent(),
            "Accept": "application/json",
            "x-goog-api-client": f"gl-node/22.0.0 antigravity/{client_version()}",
        }

    async def _refresh_version(self, account) -> None:
        """Узнать актуальную версию Antigravity (раз в VERSION_TTL); при любой беде остаётся прежняя."""
        if (not VERSION_CHECK or pinned_version()
                or time.time() - float(_client_version["checked"]) < VERSION_TTL):
            return
        _client_version["checked"] = time.time() - VERSION_TTL + 900
        try:
            session = await http.get_session(self.proxy_for(account))
            resp = await session.get(VERSION_URL, timeout=15)
            if resp.status_code == 200:
                value = str((resp.json() or {}).get("productVersion") or "")
                if _VERSION_RE.fullmatch(value):
                    _client_version["value"] = value
                    _client_version["checked"] = time.time()
        except Exception:
            pass

    async def _project_id(self, account, token: str, *, force: bool = False) -> str:
        """Возвращает cloudaicompanionProject, при необходимости проходя онбординг.

        Раньше при пустом ответе loadCodeAssist сюда подставлялся выдуманный
        «memuai-xxxx», и cloudcode-pa на каждый чат отвечал HTTP 403. Теперь
        проект либо приходит от Google, либо аккаунт честно помечается как
        непровизионированный.
        """
        cached = str(account.extra.get("project_id") or account.fields.get("project_id", "") or "").strip()
        if cached.startswith("memuai-"):

            cached = ""
            account.extra.pop("project_id", None)
            account.fields.pop("project_id", None)
        if cached and not force:
            return cached

        session = await http.get_session(self.proxy_for(account))
        metadata = {"ideType": "ANTIGRAVITY", "platform": "PLATFORM_UNSPECIFIED",
                    "pluginType": "GEMINI"}
        try:
            resp = await session.post(
                f"{PROD_ENDPOINT}/v1internal:loadCodeAssist",
                headers=self._api_headers(token),
                json={"metadata": metadata}, timeout=60)
        except Exception as e:
            raise ProviderNetworkError(f"loadCodeAssist: {e}")
        if resp.status_code in (401, 403):
            info = parse_google_error(str(resp.text))
            if resp.status_code == 403 and info.reason == "VALIDATION_REQUIRED":

                raise ProviderServerError(_verification_message(info), 503)
            raise ProviderAuthError(
                f"Google rejected the token on loadCodeAssist (HTTP {resp.status_code}): "
                f"{_error_message(str(resp.text))[:200]}")
        if resp.status_code != 200:
            raise ProviderServerError(
                f"loadCodeAssist: HTTP {resp.status_code}: {str(resp.text)[:200]}", resp.status_code)
        data = resp.json() or {}
        tier = (data.get("paidTier") or data.get("currentTier") or {})
        if isinstance(tier, dict) and tier.get("name"):
            _tier_cache[str(getattr(account, "id", ""))] = (time.time(), str(tier["name"]))
        pid = str(data.get("cloudaicompanionProject") or "").strip()
        if not pid:
            pid = await self._onboard(account, token, data, metadata)
        if not pid:
            raise ProviderAuthError(
                "Google did not return a Code Assist project for this account. Open "
                "antigravity.google, finish the account setup and sign in again.")
        account.extra["project_id"] = pid
        from ..state import state as app_state
        app_state.save_accounts()
        return pid

    async def _onboard(self, account, token: str, load_data: Dict[str, Any],
                       metadata: Dict[str, Any]) -> str:
        """onboardUser — обязательный шаг для новых аккаунтов Code Assist."""
        tier = (load_data.get("currentTier") or load_data.get("paidTier")
                or {"id": "free-tier"})
        tier_id = str(tier.get("id") or "free-tier")
        session = await http.get_session(self.proxy_for(account))
        payload = {"tierId": tier_id, "metadata": metadata}
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                resp = await session.post(
                    f"{PROD_ENDPOINT}/v1internal:onboardUser",
                    headers=self._api_headers(token), json=payload, timeout=60)
            except Exception as e:
                raise ProviderNetworkError(f"onboardUser: {e}")
            if resp.status_code in (401, 403):
                raise ProviderAuthError(
                    f"Google rejected the Antigravity onboarding (HTTP {resp.status_code}): "
                    f"{str(resp.text)[:200]}")
            if resp.status_code != 200:
                raise ProviderServerError(
                    f"onboardUser: HTTP {resp.status_code}: {str(resp.text)[:200]}",
                    resp.status_code)
            body = resp.json() or {}
            project = ((body.get("response") or {}).get("cloudaicompanionProject") or {})
            pid = str(project.get("id") or "").strip()
            if body.get("done") or pid:
                return pid
            await asyncio.sleep(3)
        return ""

    @staticmethod
    def _thinking_config(upstream: str, cfg: Dict[str, Any], params: Dict[str, Any],
                         requested: str = "") -> Optional[Dict[str, Any]]:
        """thinkingConfig запроса или None.

        Claude «(Thinking)» думает только по явному бюджету — без него модель
        отвечает сразу, и «Thinking» в названии было бы пустым словом. Gemini
        уровень берёт из id (-low/-medium/-high); здесь только просим вернуть
        краткое изложение мыслей. Исключение — «tiered»-модели (3.7 / 3.8 Flash):
        id у них один, а уровень передаётся полем ``thinkingLevel``.
        """
        low = str(upstream or "").lower()
        level = models.request_level(requested or upstream, str(params.get("effort") or ""))
        if level:
            config: Dict[str, Any] = {"thinkingLevel": level}
            if str(cfg.get("show_thoughts", "on")).lower() != "off":
                config["includeThoughts"] = True
            return config
        if low.startswith("claude") and models.is_thinking(upstream):
            effort = str(params.get("effort") or "auto").lower()
            if effort in EFFORT_BUDGETS:
                budget = EFFORT_BUDGETS[effort]
            else:
                raw = str(cfg.get("thinking_budget") if cfg.get("thinking_budget") is not None else "").strip()
                try:

                    budget = int(raw) if raw else DEFAULT_THINKING_BUDGET
                except ValueError:
                    budget = DEFAULT_THINKING_BUDGET
            if budget < 1024:
                return None
            return {"includeThoughts": True, "thinkingBudget": min(budget, 64000)}
        if low.startswith("gemini") and str(cfg.get("show_thoughts", "on")).lower() != "off":
            return {"includeThoughts": True}
        return None

    async def chat_stream(self, account, model: str, messages: List[Dict[str, Any]],
                          params: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
        token = await self._ensure_token(account)
        project = await self._project_id(account, token)
        requested_model = model
        model = self._request_model(account, model)
        system_text = "\n\n".join(content_to_text(m.get("content")) for m in messages
                                    if m.get("role") in ("system", "developer")
                                    and content_to_text(m.get("content")))

        def antigravity_parts(content: Any) -> List[Dict[str, Any]]:
            if isinstance(content, str):
                return [{"text": content}] if content else []
            out: List[Dict[str, Any]] = []
            for part in content if isinstance(content, list) else []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") in ("text", "input_text", "output_text") and part.get("text"):
                    out.append({"text": str(part["text"])})
                elif part.get("type") in ("input_file", "file", "file_url"):
                    raise ProviderServerError(
                        "Antigravity accepts images as attachments, not arbitrary files.", 400)
                elif part.get("type") in ("image_url", "input_image", "image"):
                    image = part.get("image_url") or part.get("image") or part.get("url") or ""
                    url = image.get("url", "") if isinstance(image, dict) else str(image)
                    match = re.match(r"^data:(image/[^;,]+);base64,(.+)$", url, re.I | re.S)
                    if not match:
                        raise ProviderServerError(
                            "Antigravity accepts images only as base64 data URLs. "
                            "Удалите удалённую ссылку или загрузите файл напрямую.", 400)
                    try:
                        base64.b64decode(match.group(2), validate=True)
                    except Exception:
                        raise ProviderServerError(
                            "The Antigravity attachment contains malformed base64 data.", 400)
                    out.append({"inlineData": {"mimeType": match.group(1).lower(),
                                                "data": match.group(2)}})
            return out

        contents: List[Dict[str, Any]] = []
        for message in messages:
            if message.get("role") in ("system", "developer"):
                continue
            parts = antigravity_parts(message.get("content"))
            if parts:
                contents.append({"role": "user" if message.get("role") == "user" else "model",
                                 "parts": parts})
        if not contents:
            contents.append({"role": "user", "parts": [{"text": "(empty message)"}]})

        req_parts: List[Dict[str, Any]] = [{"text": "You are Antigravity, a powerful agentic AI coding assistant."},
                                           {"text": "\n--- [SYSTEM_PROMPT_END] ---"}]
        if system_text:
            req_parts.append({"text": system_text})
        cfg = config_store.provider_cfg(self.id)
        try:
            max_tokens = max(256, min(200_000, int(cfg.get("max_output_tokens") or 64000)))
        except (TypeError, ValueError):
            max_tokens = 64000
        generation: Dict[str, Any] = {"maxOutputTokens": max_tokens}
        thinking = self._thinking_config(model, cfg, params, requested_model)
        if thinking:
            generation["thinkingConfig"] = thinking
            budget = int(thinking.get("thinkingBudget") or 0)
            if budget and generation["maxOutputTokens"] < budget + 2048:
                generation["maxOutputTokens"] = budget + 2048
        request: Dict[str, Any] = {
            "contents": contents,
            "sessionId": hashlib.md5(json.dumps(contents[0], sort_keys=True).encode()).hexdigest()[:16],
            "systemInstruction": {"role": "user", "parts": req_parts},
            "generationConfig": generation,
        }
        if str(cfg.get("safety", "off")).lower() != "default":
            learned = _reduced_safety.get(model)
            if learned != "none":
                request["safetySettings"] = SAFETY[:4] if learned == "four" else SAFETY
        if params.get("web_search"):

            request["tools"] = [{"googleSearch": {}}]
        body = {
            "model": model,
            "userAgent": user_agent(),
            "requestType": "agent",
            "project": project,
            "requestId": f"agent-{uuid.uuid4()}",
            "request": request,
        }

        choice = str(cfg.get("endpoint", "auto") or "auto").lower()
        queue = ordered_endpoints(choice)
        last_err: Optional[ProviderError] = None

        verdict: Optional[ProviderError] = None
        tried: set = set()
        waited: set = set()
        while queue:
            base = queue.pop(0)
            try:
                session = await http.get_session(self.proxy_for(account))
                resp = await session.post(
                    f"{base}/v1internal:streamGenerateContent",
                    params={"alt": "sse"},
                    headers={**self._api_headers(token), "Accept": "text/event-stream"},
                    json=body, stream=True, timeout=config_store.settings.request_timeout)
            except Exception as e:
                last_err = ProviderNetworkError(f"Сеть: {e}")
                continue
            status = resp.status_code
            if status == 200:
                _endpoint_worked(base)
                try:
                    async for event in self._iter_sse(resp):
                        yield event
                except ProviderModelUnavailableError:

                    models.mark_retired(body["model"])
                    raise
                finally:

                    await http.close_response(resp)
                self._after_chat(account, token, project)
                return

            head = await http.read_error_text(resp, 1500)
            await resp.aclose()
            info = parse_google_error(head)
            host = base.split("//")[1][:32]

            if status == 401:
                if "token" not in tried:

                    tried.add("token")
                    token = await self._ensure_token(account, force=True, stale=token)
                    queue.insert(0, base)
                    continue
                raise ProviderAuthError(f"Google rejected the token (HTTP {status})")

            if status == 429:

                last_err = self._classify_429(head, base)
                if isinstance(last_err, ProviderQuotaError):
                    hit_pool = quota_pool(body["model"])
                    _quota_hits.setdefault(str(getattr(account, "id", "")), {})[hit_pool] = time.time()

                    self._remember(account, hit_pool, 0.0, None)
                    raise last_err
                wait = info.retry_after
                if 0 < wait <= MAX_INLINE_WAIT and base not in waited:
                    waited.add(base)
                    await asyncio.sleep(wait + INLINE_WAIT_PAD)
                    queue.insert(0, base)
                    continue
                _endpoint_failed(base)
                continue

            if status in (403, 404, 408) or status >= 500:
                if status == 403 and info.reason == "VALIDATION_REQUIRED":

                    verdict = ProviderModelUnavailableError(_verification_message(info))
                    continue
                if status == 403 and "project" not in tried:

                    tried.add("project")
                    project = await self._project_id(account, token, force=True)
                    body["project"] = project
                    last_err = ProviderServerError(
                        "Antigravity: refreshing the Code Assist project and retrying", 502)
                    queue.insert(0, base)
                    continue
                hint = ""
                if status == 403:
                    hint = (" Google denied Code Assist access for this account — "
                            "проверьте, что вход в Antigravity завершён и тариф активен.")
                _endpoint_failed(base)

                last_err = ProviderServerError(
                    f"{host}: HTTP {status}.{hint} {_error_message(head)}".strip(), 502)
                continue

            if status in (400, 422):
                if "thinking" not in tried and "thinkingConfig" in generation and "thinking" in head.lower():
                    tried.add("thinking")
                    selected_thinking = generation["thinkingConfig"]

                    if selected_thinking.get("thinkingLevel") or selected_thinking.get("thinkingBudget"):
                        if selected_thinking.get("includeThoughts") and any(term in head.lower()
                                for term in ("includethoughts", "include_thoughts", "thought summaries")):
                            selected_thinking.pop("includeThoughts", None)
                        else:
                            raise ProviderServerError(
                                f"Antigravity rejected the requested reasoning depth for {requested_model}. "
                                "Уровень размышлений не был понижен автоматически. " + _error_message(head), status)
                    else:
                        generation.pop("thinkingConfig", None)
                    queue.insert(0, base)
                    continue
                if "safetySettings" in request and "safety_settings" in head.lower():

                    if len(request["safetySettings"]) > 4:
                        request["safetySettings"] = SAFETY[:4]
                        _reduced_safety[body["model"]] = "four"
                    else:
                        request.pop("safetySettings", None)
                        _reduced_safety[body["model"]] = "none"
                    queue.insert(0, base)
                    continue
                if _location_blocked(info):

                    last_err = ProviderServerError(
                        f"{host}: Google refused the request: {info.message} "
                        "(its location check is flaky — retry later or route this account through a proxy "
                        "in account settings)", 503)
                    continue
                if "model" not in tried:

                    tried.add("model")
                    await self._refresh_models(account, token, project, force=True)
                    renamed = self._request_model(account, requested_model)
                    if renamed != body["model"]:
                        body["model"] = renamed
                        last_err = ProviderServerError(
                            f"Antigravity: model id renamed to {renamed}, retrying", status)
                        queue.insert(0, base)
                        continue
            raise ProviderServerError(
                f"Antigravity: HTTP {status} ({body['model']}): {_error_message(head)}".strip().rstrip(":"),
                status)
        raise verdict or last_err or ProviderServerError("No Antigravity endpoint responded", 503)

    @staticmethod
    def _classify_429(body: str, endpoint: str) -> ProviderError:
        """HTTP 429 у Google: исчерпанная квота или сиюминутный троттлинг.

        Разница важна: квота отправляет аккаунт к следующему (в этом запросе),
        троттлинг — только повод пробовать другой эндпоинт или повторить запрос.
        Срок из ответа (``quota will reset after 3s``) решает: секунды — это
        троттлинг, часы — квота.
        """
        head = (body or "")[:400]
        low = head.lower()
        host = endpoint.split("//")[-1][:40]
        info = parse_google_error(head)
        short = 0 < info.retry_after <= 120
        daily = (info.reason == "QUOTA_EXHAUSTED" or info.retry_after >= 900
                 or "daily" in low or "per day" in low or "quota exhausted" in low)
        if daily and not short:
            when = f" (reset in ~{int(info.retry_after // 60)} min)" if info.retry_after >= 60 else ""
            return ProviderQuotaError(
                f"The Antigravity quota is used up (HTTP 429){when}. {_error_message(head)[:200]}".strip())
        return ProviderRateLimitError(
            f"{host}: Antigravity is throttling requests (HTTP 429). "
            f"Обычно это ограничение частоты запросов или нехватка мощности Google, а не исчерпанный лимит. "
            f"{_error_message(head)[:200]}".strip())

    async def _iter_sse(self, resp) -> AsyncIterator[Dict[str, Any]]:
        buf_data: List[str] = []
        produced = False
        finish = ""
        blocked = ""
        done = False
        usage: Dict[str, Any] = {}

        def read(raw: str) -> List[Dict[str, Any]]:
            nonlocal finish, blocked
            try:
                obj = json.loads(raw)
            except Exception:
                return []
            if not isinstance(obj, dict):
                return []
            reason, block = self._finish_info(obj)
            finish = finish or reason
            blocked = blocked or block
            meta = (obj.get("response") or {}).get("usageMetadata")
            if isinstance(meta, dict) and meta.get("promptTokenCount"):
                usage.update(meta)
            return self._parse_chunk(obj)

        try:
            async for line in http.aiter_lines_str(
                    resp, idle_timeout=config_store.settings.stream_idle_timeout):
                line = (line or "").rstrip("\r")
                if line.startswith("data:"):
                    payload = line[5:].lstrip()
                    if payload == "[DONE]":
                        done = True
                        break
                    buf_data.append(payload)
                    continue
                if not line and buf_data:
                    raw = "\n".join(buf_data)
                    buf_data = []
                    for ev in read(raw):
                        if not produced and ev["type"] == "text" and models.is_retired_text(ev["text"]):

                            raise ProviderModelUnavailableError(str(ev["text"]).strip()[:300])
                        produced = True
                        yield ev
        except asyncio.TimeoutError:
            raise ProviderNetworkError(
                f"Antigravity sent no data for over "
                f"{config_store.settings.stream_idle_timeout} с")
        if buf_data and not done:
            for ev in read("\n".join(buf_data)):
                produced = True
                yield ev
        if not produced:

            if blocked or finish in _REFUSAL_FINISH:
                raise ProviderContentFilterError(
                    f"Google refused to answer ({blocked or finish}). Rephrase the request.")
        if finish == "MAX_TOKENS" and produced:
            yield {"type": "finish", "reason": "length"}
        if produced and usage:

            yield {"type": "usage", "prompt_tokens": int(usage.get("promptTokenCount") or 0),
                   "completion_tokens": int(usage.get("candidatesTokenCount") or 0)
                   + int(usage.get("thoughtsTokenCount") or 0),
                   **({"reasoning_tokens": int(usage["thoughtsTokenCount"])} if "thoughtsTokenCount" in usage else {}),
                   **({"cached_tokens": int(usage["cachedContentTokenCount"])}
                      if int(usage.get("cachedContentTokenCount") or 0) > 0 else {})}

    @staticmethod
    def _finish_info(obj: Dict[str, Any]) -> Tuple[str, str]:
        """(finishReason, blockReason) из одного события потока."""
        response = obj.get("response") or {}
        reason = ""
        for cand in response.get("candidates") or []:
            if isinstance(cand, dict) and cand.get("finishReason"):
                reason = str(cand["finishReason"])
        feedback = response.get("promptFeedback") or {}
        return reason, str(feedback.get("blockReason") or "") if isinstance(feedback, dict) else ""

    @staticmethod
    def _parse_chunk(obj: Dict[str, Any]) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        if obj.get("error"):
            err = obj["error"]
            if not isinstance(err, dict):
                err = {"message": str(err)}
            msg = str(err.get("message") or "")
            details = err.get("details") or []
            reason = ""
            for d in details if isinstance(details, list) else []:
                if isinstance(d, dict) and d.get("reason"):
                    reason = d["reason"]
            try:
                code = int(err.get("code") or 502)
            except (TypeError, ValueError):
                code = 502
            if reason == "QUOTA_EXHAUSTED" or "quota" in msg.lower():
                raise ProviderQuotaError(f"Antigravity limit: {msg}")
            if (reason == "RATE_LIMIT_EXCEEDED" or "rate limit" in msg.lower()
                    or code == 429 or str(err.get("status") or "") == "RESOURCE_EXHAUSTED"):

                raise ProviderRateLimitError(f"Rate limit: {msg}")
            if code in (401, 403) or not 400 <= code <= 599:

                code = 502
            raise ProviderServerError(f"Antigravity: {msg}", code)
        for cand in ((obj.get("response") or {}).get("candidates") or []):
            for part in ((cand.get("content") or {}).get("parts") or []):
                if isinstance(part.get("text"), str) and part["text"]:

                    kind = "reasoning" if part.get("thought") else "text"
                    events.append({"type": kind, "text": part["text"]})
        return events

    async def _refresh_models(self, account, token: str, project: str,
                              force: bool = False) -> Dict[str, Any]:
        """Refresh the real server selector; a failed request never replaces a valid snapshot."""
        key = str(getattr(account, "id", "") or "")
        if not force and key in self._catalog_data and time.time() - _models_checked_at.get(key, 0) < MODEL_CATALOG_TTL:
            return self._catalog_data.get(key, {})
        try:
            session = await http.get_session(self.proxy_for(account))
            resp = await session.post(
                f"{PROD_ENDPOINT}/v1internal:fetchAvailableModels",
                headers=self._api_headers(token), json={"project": project}, timeout=30)
            if resp.status_code != 200:
                self._catalog_errors[key] = f"Antigravity model catalogue: HTTP {resp.status_code}"
                return {}
            data = resp.json() or {}
            if not isinstance(data, dict) or not isinstance(data.get("models"), dict) or not data["models"]:
                self._catalog_errors[key] = "Antigravity returned an empty or invalid model catalogue"
                return {}
        except Exception:
            self._catalog_errors[key] = "Could not fetch the Antigravity model catalogue; check the connection and account proxy"
            return {}
        self._catalog_errors.pop(key, None)
        _models_checked_at[key] = time.time()
        self._catalog_data[key] = data
        try:

            for item in models.from_server(data):
                self._display[item.public] = item.display
            found = models.from_server(data)
            if found and isinstance(getattr(account, "extra", None), dict):
                account.extra["antigravity_model_catalog"] = {
                    "checked": time.time(), "models": {item.public: {
                        "upstream": item.upstream, "display": item.display, "available": item.available}
                        for item in found}}
                from ..state import state as app_state
                app_state.save_accounts_soon()
        except Exception:
            pass
        try:
            from .. import model_info

            def record(name: str, context: Any, output: Any) -> None:
                model_info.record(self.id, name, context=context, output=output, source="api:antigravity")
            models.learn_limits(data, record)
        except Exception:
            pass
        try:

            from .. import model_capabilities
            raw_rows = []
            for upstream, info in (data.get("models") or {}).items():
                if isinstance(info, dict):
                    names = {str(upstream), models.public_id(str(upstream), str(info.get("displayName") or ""))}
                    raw_rows += [{**info, "id": name} for name in names]
            model_capabilities.record_raw(self.id, raw_rows, source="api:antigravity")
        except Exception:
            pass
        return data

    async def check(self, account) -> Tuple[Optional[bool], str]:
        try:
            token = await self._ensure_token(account)
            pid = await self._project_id(account, token)
        except ProviderAuthError as e:
            return False, str(e)
        except ProviderError as e:
            return None, str(e)
        message = f"токен работает · project: {pid[:24]}…"
        await self._refresh_version(account)
        try:
            await self._quota_buckets(account, token, pid)
            if self._notices.get(str(getattr(account, "id", ""))):
                message += " · ⚠ Google просит подтвердить аккаунт (Verify your account) — см. «Лимиты»"
        except Exception:
            pass
        return True, message

    async def dynamic_models(self, account) -> Optional[List[str]]:
        """Публичные имена моделей с сервера (служебные, дубликаты и картинки скрыты)."""
        try:
            token = await self._ensure_token(account)
            pid = await self._project_id(account, token)
            data = await self._refresh_models(account, token, pid, force=True)
            if not data:
                key = str(getattr(account, "id", "") or "")
                raise ProviderServerError(self._catalog_errors.get(key) or "Antigravity model catalogue is unavailable", 503)
            found = models.from_server(data)
            for item in found:
                self._display[item.public] = item.display
            return [item.public for item in found] or None
        except ProviderError:
            raise
        except Exception:
            return None

    async def _quota_buckets(self, account, token: str, project: str) -> Dict[str, Dict[str, Any]]:
        """model → {remainingFraction, resetTime}: retrieveUserQuota, else fetchAvailableModels."""
        key = str(getattr(account, "id", ""))
        self._notices.pop(key, None)
        try:
            session = await http.get_session(self.proxy_for(account))
            resp = await session.post(f"{PROD_ENDPOINT}/v1internal:retrieveUserQuota",
                                      headers=self._api_headers(token), json={"project": project}, timeout=30)
            if resp.status_code == 200:
                out = {}
                for bucket in (resp.json() or {}).get("buckets") or []:
                    if isinstance(bucket, dict) and bucket.get("modelId"):
                        out[str(bucket["modelId"])] = bucket
                if out:
                    await self._refresh_models(account, token, project)
                    return out
            elif resp.status_code == 403:
                info = parse_google_error(str(resp.text))
                if info.reason == "VALIDATION_REQUIRED":

                    self._notices[key] = {"url": info.url}
        except Exception:
            pass
        found = (await self._refresh_models(account, token, project, force=True)).get("models") or {}
        return {name: (info.get("quotaInfo") or {}) for name, info in found.items() if isinstance(info, dict)}

    def _remember(self, account, pool: str, remaining: float, reset: Optional[float]) -> None:
        """Замер для оценки за 7 дней. Молча ничего не делает, если аккаунт без extra."""
        if quota.record_point(getattr(account, "extra", None), pool, remaining, reset):
            try:
                from ..state import state as app_state
                app_state.save_accounts_soon()
            except Exception:
                pass

    def _groups(self, buckets: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, List[str]], List[str]]:
        """Модели по котлам (Gemini, Claude / GPT) и прочие одиночные."""
        names = [n for n in buckets if not _is_internal_model(n) and n not in _deprecated_learned]
        grouped: Dict[str, List[str]] = {"Gemini": [], "Claude / GPT": []}
        other: List[str] = []
        for name in names:
            if quota_pool(name) == "Gemini":
                grouped["Gemini"].append(name)
            elif name.lower().startswith(("claude", "gpt")):
                grouped["Claude / GPT"].append(name)
            else:
                other.append(name)
        return grouped, other

    def _spawn(self, coro) -> None:
        """Фоновая задача без ожидания: ответ клиенту от неё не зависит."""
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close()
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _after_chat(self, account, token: str, project: str) -> None:
        """После удачного запроса, в фоне: версия клиента и замер лимитов (не чаще раза в SAMPLE_EVERY)."""
        if VERSION_CHECK and time.time() - float(_client_version["checked"]) >= VERSION_TTL:
            self._spawn(self._refresh_version(account))
        if not SAMPLE_AFTER_CHAT:
            return
        key = str(getattr(account, "id", ""))
        if time.time() - _last_sample.get(key, 0.0) < SAMPLE_EVERY:
            return
        _last_sample[key] = time.time()
        self._spawn(self._sample(account, token, project))

    async def _sample(self, account, token: str, project: str) -> None:
        try:
            buckets = await self._quota_buckets(account, token, project)
            now = time.time()
            grouped, _ = self._groups(buckets)
            for title, group in grouped.items():
                states = [s for s in (quota.bucket_state(buckets.get(n)) for n in group) if s]
                short = quota.windows(states, now).get("5h")
                if short:
                    self._remember(account, title, short[0], short[1])
        except Exception:
            pass

    async def _tier_name(self, account, token: str) -> str:
        """Тариф аккаунта («Google AI Pro») из loadCodeAssist; раз в полчаса."""
        key = str(getattr(account, "id", ""))
        cached = _tier_cache.get(key)
        if cached and time.time() - cached[0] < 1800:
            return cached[1]
        try:
            session = await http.get_session(self.proxy_for(account))
            resp = await session.post(
                f"{PROD_ENDPOINT}/v1internal:loadCodeAssist", headers=self._api_headers(token),
                json={"metadata": {"ideType": "ANTIGRAVITY", "platform": "PLATFORM_UNSPECIFIED",
                                   "pluginType": "GEMINI"}}, timeout=20)
            if resp.status_code == 200:
                data = resp.json() or {}
                tier = data.get("paidTier") or data.get("currentTier") or {}
                name = str(tier.get("name") or "") if isinstance(tier, dict) else ""
                if name:
                    _tier_cache[key] = (time.time(), name)
                return name
        except Exception:
            pass
        return cached[1] if cached else ""

    async def usage(self, account) -> Optional[Dict[str, Any]]:
        """Лимиты как в настоящем Antigravity: котёл Gemini и котёл Claude/GPT.

        Строки: «5 часов» — окно, которое отдаёт Google; «неделя» — длинное
        окно, если Google его отдаёт (бесплатные аккаунты); «7 дней (оценка)» —
        наша сумма по замерам, потому что у Pro-аккаунтов недельного счётчика в
        API нет (подробности — в antigravity_quota.py).
        """
        token = await self._ensure_token(account)
        pid = await self._project_id(account, token)
        buckets = await self._quota_buckets(account, token, pid)
        if not buckets:
            return None
        now = time.time()
        extra = getattr(account, "extra", None)
        hits = _quota_hits.get(str(getattr(account, "id", "")), {})
        grouped, other = self._groups(buckets)
        rows: List[Dict[str, Any]] = []
        resets: List[float] = []
        for title, group in grouped.items():
            states = [s for s in (quota.bucket_state(buckets.get(n)) for n in group) if s]
            if not states:
                continue
            found = quota.windows(states, now)
            short, long_ = found.get("5h"), found.get("week")
            if short:
                remaining, reset = short
                row: Dict[str, Any] = {"display": f"{title} — 5 часов", "display_en": f"{title} — 5h",
                                       "used": quota.used_percent(remaining), "limit": 100, "unit": "%"}
                if reset:
                    row["reset"] = reset
                    resets.append(reset)
                hit = hits.get(title, 0)
                if hit and now - hit < 6 * 3600 and remaining > 0.02:
                    stamp = time.strftime("%H:%M", time.localtime(hit))
                    row["note"] = ("Google ответил «квота исчерпана» " + stamp
                                   + ", хотя счётчик 5-часового окна почти полон: вероятно, это недельный лимит тарифа.")
                    row["note_en"] = ("Google answered «quota exhausted» at " + stamp
                                      + " while the 5-hour counter is almost full: this is likely the plan's weekly limit.")
                rows.append(row)
                self._remember(account, title, remaining, reset)
            if long_:
                remaining, reset = long_
                week: Dict[str, Any] = {"display": f"{title} — неделя", "display_en": f"{title} — week",
                                        "used": quota.used_percent(remaining), "limit": 100, "unit": "%",
                                        "note": "недельный лимит — так его отдаёт Google",
                                        "note_en": "the weekly limit, as Google reports it"}
                if reset:
                    week["reset"] = reset
                    resets.append(reset)
                rows.append(week)
            elif short:

                spent = quota.history_windows(extra, title, now) or 0.0
                if True:
                    rows.append({
                        "display": f"{title} — за 7 дней (оценка)", "display_en": f"{title} — last 7 days (estimate)",
                        "used": spent, "limit": None, "unit": "окон по 5 ч", "unit_en": "5-hour windows",
                        "note": "Google не отдаёт недельный счётчик для этого аккаунта; это сумма расхода "
                                "5-часовых окон по замерам (страница лимитов и запросы к модели) — не меньше, чем видно",
                        "note_en": "Google does not report a weekly counter for this account; this is the sum of the "
                                   "5-hour windows spent, from samples (limits page and requests) — at least what was seen"})
        extras = []
        for name in other:
            state = quota.bucket_state(buckets.get(name))
            if state:
                extras.append({"display": name, "used": quota.used_percent(state[0]), "limit": 100, "unit": "%"})
        rows.extend(sorted(extras, key=lambda r: r["display"])[:6])
        if not rows:
            return None
        result: Dict[str, Any] = {"rows": rows}
        if resets:
            result["next_reset"] = min(resets)
        tier = await self._tier_name(account, token)
        if tier:
            result["subscription"] = tier
        notice = self._notices.get(str(getattr(account, "id", "")))
        if notice is not None:
            result["notice"] = ("Google просит подтвердить этот аккаунт («Verify your account»): пока он не "
                                "подтверждён, точный счётчик недоступен — остаток взят из списка моделей.")
            result["notice_en"] = ("Google asks to verify this account (“Verify your account”): until it is "
                                   "verified the exact counter is unavailable — the remainder comes from the model list.")
            if str(notice.get("url") or "").startswith("https://"):
                result["notice_url"] = notice["url"]
        return result

    async def oauth_start(self) -> Dict[str, Any]:
        try:
            client_id, client_secret = _oauth_client()
        except ProviderAuthError as exc:
            return {"ok": False, "message": str(exc)}
        flow_id = uuid.uuid4().hex[:12]
        state = secrets.token_urlsafe(24)
        callback_port = 51121
        flow = {"status": "waiting", "state": state, "account": None, "error": ""}
        flow["oauth_client"] = (client_id, client_secret)
        self._flows[flow_id] = flow

        spawn_background(self._run_listener(flow_id, flow, callback_port))
        for _ in range(30):
            await asyncio.sleep(0.1)
            if flow["status"] == "error" or flow.get("bound"):
                break
        if flow["status"] == "error":
            return {"ok": False, "message": flow["error"]}
        redirect_uri = f"http://localhost:{flow.get('port', callback_port)}/oauth-callback"
        flow["redirect_uri"] = redirect_uri
        params = urlencode({
            "client_id": client_id, "redirect_uri": redirect_uri,
            "response_type": "code", "scope": SCOPES,
            "access_type": "offline", "prompt": "consent",
            "state": state,
        })
        url = f"{AUTH_URL}?{params}"
        webbrowser.open(url)
        return {"ok": True, "flow_id": flow_id, "url": url, "port": flow.get("port", callback_port)}

    async def oauth_status(self, flow_id: str) -> Dict[str, Any]:
        flow = self._flows.get(flow_id)
        if not flow:
            return {"status": "error", "message": "flow not found"}
        if flow["status"] == "done" and flow.get("account"):
            acc = flow.pop("account")
            self._flows.pop(flow_id, None)
            return {"status": "success", "account": acc}
        return {"status": flow["status"], "message": flow.get("error", "")}

    async def _exchange_code(self, flow: Dict[str, Any], code: str) -> None:

        client_id, client_secret = flow.get("oauth_client") or _oauth_client()
        session = await http.get_session("")
        resp = await session.post(TOKEN_URL, data={
            "client_id": client_id, "client_secret": client_secret,
            "code": code, "grant_type": "authorization_code",
            "redirect_uri": flow.get("redirect_uri")
            or f"http://localhost:{flow['port']}/oauth-callback",
        }, timeout=30)
        if resp.status_code != 200:
            flow["status"], flow["error"] = "error", f"Обмен кода не удался (HTTP {resp.status_code})"
            return
        data = resp.json()
        email = ""
        try:
            me = await session.get("https://www.googleapis.com/oauth2/v2/userinfo",
                                   headers={"Authorization": f"Bearer {data.get('access_token')}"}, timeout=20)
            email = (me.json() or {}).get("email", "")
        except Exception:
            pass
        from ..state import state as app_state
        acc = app_state.add_account("antigravity", fields={}, label=email or "Antigravity (Google)")
        acc.extra["oauth"] = {"access_token": data.get("access_token"),
                              "refresh_token": data.get("refresh_token"),
                              "expires_at": time.time() + int(data.get("expires_in", 3600)) - 300}
        app_state.save_accounts()
        flow["status"] = "done"
        flow["account"] = acc.public_dict()
        from ..model_refresh import refresher
        spawn_background(refresher.refresh(providers=[self.id], reason="oauth-connected"))

    async def _run_listener(self, flow_id: str, flow: Dict[str, Any], port: int) -> None:
        flow["port"] = port
        done = asyncio.Event()

        def page(text: str) -> bytes:
            body = ("<html><head><title>MemuAI</title></head>"
                    "<body style='font-family:sans-serif;background:#0b0e14;color:#e7eaf2;"
                    "display:flex;align-items:center;justify-content:center;height:100vh'>"
                    f"<h2>{text}</h2></body></html>").encode("utf-8")
            return (b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                    b"Content-Length: " + str(len(body)).encode() +
                    b"\r\nConnection: close\r\n\r\n" + body)

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                raw = await asyncio.wait_for(reader.read(8192), timeout=120)
                line = raw.decode("utf-8", errors="replace").split("\r\n")[0]
                path = line.split(" ")[1] if " " in line else ""
                if path.startswith("/oauth-callback"):
                    q = parse_qs(urlparse(path).query)
                    code = (q.get("code") or [""])[0]
                    if code and (q.get("state") or [""])[0] == flow["state"]:
                        flow["code"] = code
                        done.set()

                        writer.write(page("Вход выполнен. Вернитесь в MemuAI Proxy — "
                                          "окно можно закрыть."))
                        await writer.drain()
                        return
                writer.write(page("Окно можно закрыть…"))
                await writer.drain()
            except Exception:
                pass
            finally:
                try:
                    writer.close()
                except Exception:
                    pass

        server = None
        last_err = None

        for p in range(port, port + 11):
            try:
                server = await asyncio.start_server(handle, "127.0.0.1", p)
                flow["port"] = p
                flow["bound"] = True
                break
            except OSError as e:
                last_err = e
                continue
        try:
            if not server:
                flow["status"] = "error"
                flow["error"] = f"Не удалось занять порт {port}+10: {last_err}"
                return
            try:
                await asyncio.wait_for(done.wait(), timeout=600)
            except asyncio.TimeoutError:
                flow["status"], flow["error"] = "error", "Ожидание входа истекло (10 минут)"
                return
            try:
                await self._exchange_code(flow, flow["code"])
            except Exception as e:

                flow["status"], flow["error"] = "error", f"Ошибка входа: {type(e).__name__}: {e}"
        finally:
            if server:
                try:
                    server.close()
                    await server.wait_closed()
                except Exception:
                    pass
