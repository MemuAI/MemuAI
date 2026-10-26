"""HTTP-клиент Genspark на curl_cffi.

Genspark стоит за Cloudflare с JA3-фильтрацией, поэтому обычные HTTP-библиотеки
попадают под блок. curl_cffi имитирует реальный TLS-отпечаток Chrome.
"""
from __future__ import annotations

import asyncio
import base64
import json
from typing import Any, Dict, List, Optional, Tuple

from curl_cffi.requests import AsyncSession

class GensparkError(Exception):
    """Базовая ошибка апстрима."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.status = status

class GensparkAuthError(GensparkError):
    """Кука не принята (not login / 401 / 403)."""

class GensparkRateLimitError(GensparkError):
    """Rate limit аккаунта (кратковременная пауза)."""

class GensparkFreeLimitError(GensparkError):
    """Исчерпан дневной лимит аккаунта."""

class GensparkCloudflareError(GensparkError):
    """Cloudflare: challenge или блок."""

class GensparkServerError(GensparkError):
    """Прочая ошибка апстрима (5xx / неожиданный ответ)."""

class GensparkContentFilterError(GensparkError):
    """The upstream model refused the prompt (safety filter / policy block).

    Retrying on another account cannot help, so the request must fail fast
    with a clear message instead of waiting for the idle timeout.
    """

class GensparkModelError(GensparkError):
    """The requested model is unavailable for this account or was retired."""

class GensparkNetworkError(GensparkError):
    """Сеть: таймаут, DNS, прокси недоступен."""

def classify_status(status_code: int, body_head: str = "") -> Optional[GensparkError]:
    """Сопоставляет HTTP-статус и начало тела с типом ошибки."""
    if status_code in (401, 403):
        if "just a moment" in body_head.lower() or "_cf_chl" in body_head:
            return GensparkCloudflareError("Cloudflare challenge (HTTP %d)" % status_code, status_code)
        return GensparkAuthError("The cookie was rejected (HTTP %d)" % status_code, status_code)
    if status_code == 429:
        return GensparkRateLimitError("Rate limit (HTTP 429)", status_code)
    if status_code >= 500:
        return GensparkServerError("Ошибка Genspark (HTTP %d)" % status_code, status_code)
    return None

def looks_like_cloudflare(text: str) -> bool:
    t = text[:4000].lower()
    markers = (
        "<title>just a moment",
        "window._cf_chl_opt",
        "challenge-platform",
        "cf-browser-verification",
        "attention required! | cloudflare",
        "sorry, you have been blocked",
    )
    return any(m in t for m in markers)

class GensparkClient:
    """Кэширует AsyncSession по комбинации (прокси, отпечаток, verify_ssl)."""

    FALLBACK_CHAIN = ["chrome136", "chrome150", "chrome131"]

    def __init__(self, api_base: str, impersonate: str = "chrome136",
                 verify_ssl: bool = True, user_agent: str = "") -> None:
        self.api_base = api_base.rstrip("/")
        self.impersonate = impersonate
        self.verify_ssl = verify_ssl
        self.user_agent = user_agent or self._ua_for(impersonate)
        self._sessions: Dict[str, AsyncSession] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _ua_for(impersonate: str) -> str:
        import re as _re
        m = _re.search(r"(\d+)", impersonate)
        ver = m.group(1) if m else "136"
        return (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{ver}.0.0.0 Safari/537.36")

    def _set_fingerprint(self, impersonate: str) -> None:
        self.impersonate = impersonate
        if not self.user_agent or "Chrome/" not in self.user_agent:
            self.user_agent = self._ua_for(impersonate)
        self.user_agent = _re_chrome_bump(self.user_agent, impersonate)

    def _session_key(self, proxy: str) -> str:
        return f"{proxy}|{self.impersonate}|{self.verify_ssl}"

    async def _get_session(self, proxy: str) -> AsyncSession:
        key = self._session_key(proxy)
        async with self._lock:
            session = self._sessions.get(key)
            if session is None:
                kwargs: Dict[str, Any] = {
                    "impersonate": self.impersonate,
                    "verify": self.verify_ssl,
                }
                if proxy:
                    kwargs["proxies"] = {"http": proxy, "https": proxy}
                session = AsyncSession(**kwargs)
                self._sessions[key] = session
            return session

    async def reconfigure(self, api_base: str | None = None, impersonate: str | None = None,
                          verify_ssl: bool | None = None, user_agent: str | None = None) -> None:
        async with self._lock:
            if api_base is not None:
                self.api_base = api_base.rstrip("/")
            if impersonate is not None:
                self.impersonate = impersonate
            if verify_ssl is not None:
                self.verify_ssl = verify_ssl
            if user_agent:
                self.user_agent = user_agent
            old, self._sessions = self._sessions, {}
        for s in old.values():
            try:
                await s.close()
            except Exception:
                pass

    async def close(self) -> None:
        async with self._lock:
            old, self._sessions = self._sessions, {}
        for s in old.values():
            try:
                await s.close()
            except Exception:
                pass

    async def download_image(self, cookie: str, url: str, *, proxy: str = ""):
        """Download a protected Genspark result with the account session."""
        return await self._request("GET", url, cookie=cookie, proxy=proxy,
                                   accept="image/*", referer="/ai_image", timeout=90)

    def _headers(self, cookie: str, accept: str = "*/*",
                 referer: str = "/agents?type=ai_chat") -> Dict[str, str]:
        return {
            "Accept": accept,
            "Origin": self.api_base,
            "Referer": self.api_base + referer,
            "User-Agent": self.user_agent,
            **({"Cookie": cookie} if cookie else {}),
        }

    async def _request(self, method: str, path: str, *, cookie: str = "", proxy: str = "",
                       json_body: Any = None, accept: str = "application/json",
                       referer: str = "/agents?type=ai_chat", timeout: int = 60,
                       stream: bool = False):
        """Запрос с автоподбором TLS-отпечатка: при 403 (Cloudflare) пробуем
        запасные отпечатки, прежде чем отдать ошибку наверх."""
        attempts = [self.impersonate] + [f for f in self.FALLBACK_CHAIN if f != self.impersonate]
        original = (self.impersonate, self.user_agent)
        resp = None
        for i, imp in enumerate(attempts):
            if i > 0:
                self._set_fingerprint(imp)
            resp = await self._request_once(method, path, cookie=cookie, proxy=proxy,
                                            json_body=json_body, accept=accept,
                                            referer=referer, timeout=timeout, stream=stream)
            if resp.status_code != 403:
                return resp
            if i == len(attempts) - 1:

                self.impersonate, self.user_agent = original
                return resp

            from .. import http as _http
            await _http.close_response(resp)
        return resp

    async def _request_once(self, method: str, path: str, *, cookie: str = "", proxy: str = "",
                            json_body: Any = None, accept: str = "application/json",
                            referer: str = "/agents?type=ai_chat", timeout: int = 60,
                            stream: bool = False):
        session = await self._get_session(proxy)
        url = path if path.startswith("http") else self.api_base + path
        try:
            resp = await session.request(
                method, url,
                headers=self._headers(cookie, accept=accept, referer=referer),
                **({"json": json_body} if json_body is not None else {}),
                timeout=timeout,
                stream=stream,
            )
        except asyncio.TimeoutError:
            raise GensparkNetworkError(f"Таймаут соединения с {url}")
        except Exception as e:
            raise GensparkNetworkError(f"Network error: {type(e).__name__}: {e}")
        return resp

    def build_agent_payload(self, *, model: str, user_s_input: str,
                            messages: list, project_id: str | None,
                            enable_search: bool, is_private: bool = True) -> Dict[str, Any]:
        """Тело для POST /api/agent/ask_proxy — как у веб-интерфейса (ai_chat)."""
        return {
            "ai_chat_model": model,
            "ai_chat_enable_search": enable_search,
            "ai_chat_disable_personalization": False,
            "use_moa_proxy": "," in model,
            "moa_models": [],
            "writingContent": None,
            "type": "ai_chat",
            "project_id": project_id,
            "messages": messages,
            "user_s_input": user_s_input,
            "g_recaptcha_token": "",
            "is_private": is_private,
            "push_token": "",
            "session_state": {"steps": [], "messages": messages},
        }

    def build_copilot_payload(self, *, model: str, messages: list,
                              request_web_knowledge: bool) -> Dict[str, Any]:
        """Тело для POST /api/copilot/ask (COPILOT_MOA_CHAT) — резервный бэкенд."""
        return {
            "type": "COPILOT_MOA_CHAT",
            "current_query_string": "type=COPILOT_MOA_CHAT",
            "messages": messages,
            "action_params": {},
            "extra_data": {
                "models": [model],
                "run_with_another_model": False,
                "writingContent": None,
                "request_web_knowledge": request_web_knowledge,
            },
        }

    async def stream_chat(self, cookie: str, payload: Dict[str, Any], *, backend: str,
                          proxy: str = "", timeout: int = 120):
        """Открывает SSE-стрим с ответом Genspark.

        backend: "agent" → /api/agent/ask_proxy, "copilot" → /api/copilot/ask.
        Возвращает ответ curl_cffi со стримом (caller обязан закрыть).
        """
        path = "/api/agent/ask_proxy" if backend == "agent" else "/api/copilot/ask"
        resp = await self._request(
            "POST", path, cookie=cookie, proxy=proxy,
            json_body=payload, accept="text/event-stream",
            referer="/agents?type=ai_chat" if backend == "agent" else "/",
            timeout=timeout, stream=True,
        )
        return resp

    async def image_ask(self, cookie: str, payload: Dict[str, Any], *,
                        proxy: str = "", timeout: int = 300):
        """POST /api/copilot/ask (COPILOT_MOA_IMAGE), полный ответ."""
        return await self._request("POST", "/api/copilot/ask", cookie=cookie, proxy=proxy,
                                   json_body=payload, accept="text/event-stream", referer="/ai_image",
                                   timeout=timeout, stream=False)

    async def upload_private_file(self, cookie: str, data_url: str, name: str = "file",
                                  *, proxy: str = "", timeout: int = 120) -> Dict[str, Any]:
        """Загружает не-графический файл в private storage Genspark."""
        if not data_url.startswith("data:") or "," not in data_url:
            raise GensparkError("Файл должен быть передан как data URL")
        header, encoded = data_url.split(",", 1)
        mime = header[5:].split(";", 1)[0] or "application/octet-stream"
        try:
            raw = base64.b64decode(encoded) if ";base64" in header else encoded.encode("utf-8")
        except Exception as exc:
            raise GensparkError(f"Could not decode the file: {exc}")
        meta_resp = await self._request("GET", "/api/get_upload_personal_image_url",
                                        cookie=cookie, proxy=proxy, accept="*/*",
                                        referer="/agents?type=ai_chat", timeout=30)
        err = classify_status(meta_resp.status_code, meta_resp.text[:1000])
        if err:
            raise err
        try:
            meta = meta_resp.json().get("data") or {}
            upload_url = str(meta["upload_image_url"])
            storage_url = str(meta["private_storage_url"])
        except Exception:
            raise GensparkServerError("Genspark returned no upload URL for the file")
        session = await self._get_session(proxy)
        try:
            response = await session.request(
                "PUT", upload_url, data=raw, timeout=timeout,
                headers={"Accept": "*/*", "x-ms-blob-type": "BlockBlob",
                         "Content-Type": "application/octet-stream"},
            )
        except Exception as e:
            raise GensparkNetworkError(f"File upload failed: {type(e).__name__}: {e}")
        if response.status_code < 200 or response.status_code >= 300:
            raise GensparkServerError(f"Could not upload the file to Genspark (HTTP {response.status_code})")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else mime.rsplit("/", 1)[-1]
        return {"type": "private_file", "private_file": {
            "name": name or "file", "type": mime, "size": len(raw), "ext": ext,
            "private_storage_url": storage_url,
        }}

    async def ig_tasks_status(self, cookie: str, task_ids: List[str], *,
                              proxy: str = "", timeout: int = 600):
        """SSE-стрим статусов задач генерации."""
        return await self._request("POST", "/api/ig_tasks_status", cookie=cookie, proxy=proxy,
                                   json_body={"task_ids": task_ids}, accept="text/event-stream",
                                   referer="/ai_image",
                                   timeout=timeout, stream=True)

    async def is_login(self, cookie: str, proxy: str = "") -> Tuple[Optional[bool], str]:
        """Лёгкая проверка куки: GET /api/is_login.

        Возвращает (True/False/None, сообщение): None — сеть недоступна,
        статус куки неизвестен (не считаем куку битой).
        """
        try:
            resp = await self._request("GET", "/api/is_login", cookie=cookie, proxy=proxy,
                                       accept="application/json", referer="/", timeout=20)
        except GensparkNetworkError as e:
            return None, f"сеть недоступна: {e}"
        except GensparkError as e:
            return None, str(e)
        body = resp.text
        if resp.status_code != 200:
            err = classify_status(resp.status_code, body)
            if isinstance(err, GensparkCloudflareError):
                return None, str(err)
            if err is not None:
                return False, str(err)
            return False, f"HTTP {resp.status_code}"
        if looks_like_cloudflare(body):
            return None, "Cloudflare challenge"
        try:
            data = json.loads(body)
        except Exception:
            return False, "Unexpected response from /api/is_login"
        d = data.get("data") or {}
        if d.get("is_login") is False:
            return False, "кука не авторизована (возможно, истекла — перезайдите в Genspark)"
        status = data.get("status")
        if status == 0:
            return True, "ок"
        if status == -5:
            return False, "кука не авторизована"
        return False, str(data.get("message") or f"status={status}")

    async def user_info(self, cookie: str, proxy: str = "") -> Dict[str, Any]:
        """GET /api/user — email/план, насколько Genspark отдаёт. Best effort."""
        try:
            resp = await self._request("GET", "/api/user", cookie=cookie, proxy=proxy,
                                       accept="application/json", referer="/", timeout=20)
            body = resp.text
        except GensparkError:
            return {}
        try:
            data = json.loads(body)
        except Exception:
            return {}
        d = data.get("data") if isinstance(data.get("data"), dict) else {}
        info = {}
        for key_src, key_dst in (("email", "email"), ("user_name", "user_name"),
                                 ("username", "user_name"), ("plan_type", "plan"),
                                 ("planType", "plan"), ("is_pro", "is_pro")):
            if key_src in d:
                info[key_dst] = d[key_src]
        return info

    async def models_config(self, cookie: str = "", proxy: str = "") -> Dict[str, Any]:
        """GET /api/models_config — каталог image/audio/video моделей (без авторизации)."""
        resp = await self._request("GET", "/api/models_config", cookie=cookie, proxy=proxy,
                                   accept="application/json", referer="/", timeout=30)
        body = resp.text
        if resp.status_code != 200:
            raise GensparkServerError(f"models_config: HTTP {resp.status_code}", resp.status_code)
        try:
            data = json.loads(body)
        except Exception as e:
            raise GensparkServerError(f"models_config: not JSON: {e}")
        return data.get("data") or {}

    async def delete_project(self, cookie: str, project_id: str, proxy: str = "") -> None:
        if not project_id:
            return
        try:
            await self._request("GET", f"/api/project/delete?project_id={project_id}",
                                cookie=cookie, proxy=proxy, accept="application/json",
                                referer="/", timeout=15)
        except GensparkError:
            pass

    async def fetch_text(self, url: str, proxy: str = "", timeout: int = 30) -> str:
        resp = await self._request("GET", url, cookie="", proxy=proxy,
                                   accept="*/*", referer="/", timeout=timeout)
        return resp.text

def _re_chrome_bump(ua: str, impersonate: str) -> str:
    import re as _re
    m = _re.search(r"(\d+)", impersonate)
    if not m:
        return ua
    return _re.sub(r"Chrome/\d+(\.\d+)*", f"Chrome/{m.group(1)}.0.0.0", ua)
