"""Провайдер «Собственный API»: любой OpenAI-совместимый эндпоинт.

Пользователь добавляет название (оно же префикс моделей), base_url, ключ —
и модели этого эндпоинта появляются в общем каталоге как <имя>-<модель>.
Плюс сквозной прокси /p/<имя>/<path> для обхода CORS.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from .. import reasoning_levels

_MODEL_DENIED_RE = re.compile(r"\bmodels?\b|модел|this request|этому запросу|not allowed to use|"
                              r"does not have access to|no access to", re.I)

from .base import (
    Field,
    Provider,
    ProviderAuthError,
    ProviderError,
    ProviderNetworkError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderServerError,
    messages_have_images,
    retry_after_seconds,
)
from ..config import config_store
from . import http

PRESETS: List[Dict[str, Any]] = [
    {
        "id": "openrouter", "title": "OpenRouter", "icon": "◇",
        "name": "openrouter", "base_url": "https://openrouter.ai/api/v1",
        "key_url": "https://openrouter.ai/keys",
        "hint": "Ключ вида sk-or-v1-… Модели подтянутся автоматически.",
        "hint_en": "Key looks like sk-or-v1-…. Models are pulled automatically.",
        "models": [],
        "headers": {"HTTP-Referer": "https://memuai.local", "X-Title": "MemuAI"},
    },
    {
        "id": "openai", "title": "OpenAI", "icon": "◉",
        "name": "openai", "base_url": "https://api.openai.com/v1",
        "key_url": "https://platform.openai.com/api-keys",
        "hint": "Ключ вида sk-… из личного кабинета OpenAI.",
        "hint_en": "An sk-… key from the OpenAI dashboard.",
        "models": [],
        "image_models": ["gpt-image-1"],
    },
    {
        "id": "anthropic", "title": "Anthropic", "icon": "✦",
        "name": "anthropic", "base_url": "https://api.anthropic.com/v1",
        "key_url": "https://console.anthropic.com/settings/keys",
        "hint": "Ключ sk-ant-…. Anthropic отдаёт OpenAI-совместимый /v1/chat/completions.",
        "hint_en": "An sk-ant-… key. Anthropic serves an OpenAI-compatible /v1/chat/completions.",
        "models": ["claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5"],
        "headers": {"anthropic-version": "2023-06-01"},
    },
    {
        "id": "google-ai-studio", "title": "Google AI Studio", "icon": "✧",
        "name": "aistudio",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key_url": "https://aistudio.google.com/apikey",
        "hint": "Ключ AI Studio (AIza…). Работает OpenAI-совместимый слой Gemini.",
        "hint_en": "An AI Studio key (AIza…). Uses the OpenAI-compatible Gemini layer.",
        "models": [],
    },
    {
        "id": "deepseek", "title": "DeepSeek", "icon": "◈",
        "name": "deepseek", "base_url": "https://api.deepseek.com/v1",
        "key_url": "https://platform.deepseek.com/api_keys",
        "hint": "Ключ sk-… из платформы DeepSeek.",
        "hint_en": "An sk-… key from the DeepSeek platform.",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    {
        "id": "groq", "title": "Groq", "icon": "⚡",
        "name": "groq", "base_url": "https://api.groq.com/openai/v1",
        "key_url": "https://console.groq.com/keys",
        "hint": "Ключ gsk_… из консоли Groq.",
        "hint_en": "A gsk_… key from the Groq console.",
        "models": [],
    },
    {
        "id": "xai", "title": "xAI (Grok)", "icon": "✕",
        "name": "xai", "base_url": "https://api.x.ai/v1",
        "key_url": "https://console.x.ai",
        "hint": "Ключ xai-… из консоли xAI.",
        "hint_en": "An xai-… key from the xAI console.",
        "models": [],
    },
    {
        "id": "nvidia", "title": "NVIDIA NIM", "icon": "▣",
        "name": "nvidia", "base_url": "https://integrate.api.nvidia.com/v1",
        "key_url": "https://build.nvidia.com",
        "hint": "Ключ nvapi-… из NVIDIA build.",
        "hint_en": "An nvapi-… key from NVIDIA build.",
        "models": [],
    },
    {
        "id": "nanogpt", "title": "NanoGPT", "icon": "◍",
        "name": "nanogpt", "base_url": "https://nano-gpt.com/api/v1",
        "key_url": "https://nano-gpt.com/api",
        "hint": "Ключ из раздела API на nano-gpt.com.",
        "hint_en": "A key from the API section of nano-gpt.com.",
        "models": [],
    },
    {
        "id": "bedrock", "title": "AWS Bedrock", "icon": "▤",
        "name": "bedrock",
        "base_url": "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1",
        "key_url": "https://console.aws.amazon.com/bedrock",
        "hint": "Нужен Bedrock API key (долгосрочный). Замените us-east-1 в адресе "
                "на свой регион, если он другой.",
        "hint_en": "Needs a long-lived Bedrock API key. Replace us-east-1 in the URL "
                   "with your own region if it differs.",
        "edit_url": True,
        "models": [],
    },
    {
        "id": "vertex", "title": "Google Vertex AI", "icon": "△",
        "name": "vertex",
        "base_url": "https://us-central1-aiplatform.googleapis.com/v1/projects/"
                    "PROJECT_ID/locations/us-central1/endpoints/openapi",
        "key_url": "https://console.cloud.google.com/vertex-ai",
        "hint": "Замените PROJECT_ID и регион в адресе. В поле ключа — токен "
                "`gcloud auth print-access-token` (живёт около часа).",
        "hint_en": "Replace PROJECT_ID and the region in the URL. Use the output of "
                   "`gcloud auth print-access-token` as the key (valid about an hour).",
        "edit_url": True,
        "models": [],
    },
]

class CustomProvider(Provider):
    id = "custom"
    title = "Собственный API"
    icon = "⚿"
    title_en = "Custom API"
    description = "Любой OpenAI-совместимый API: ключ + эндпоинт. Модели — с вашим префиксом."
    description_en = "Any OpenAI-compatible API: key + endpoint. Models appear under your own prefix."

    def account_fields(self) -> List[Field]:
        return [
            Field("name", "Название (префикс моделей)", "text",
                  placeholder="напр. openrouter или mysite",
                  help="Модели станут: <название>-<модель>. Латиницей, цифрами и дефисом.",
                  label_en="Name (model prefix)", placeholder_en="e.g. openrouter or mysite",
                  help_en="Models become <name>-<model>. Latin letters, digits and dashes only."),
            Field("base_url", "Base URL", "url", placeholder="https://api.example.com/v1",
                  help="Корень OpenAI-совместимого API (обычно заканчивается на /v1)",
                  help_en="Root of the OpenAI-compatible API (usually ends with /v1)"),
            Field("api_protocol", "Протокол чата", "select", required=False,
                  options=["auto", "chat", "responses"], default="auto",
                  help="auto — Responses для пути …/responses, иначе Chat Completions. chat — /chat/completions; responses — /responses.",
                  label_en="Chat protocol",
                  help_en="auto — Responses for a path ending in /responses, otherwise Chat Completions. chat — /chat/completions; responses — /responses."),
            Field("chat_endpoint", "Путь API чата", "text", required=False,
                  placeholder="/v1/responses",
                  help="Пусто — стандартный путь протокола. /v1/responses — от корня сайта; responses — относительно Base URL. Разрешены query-параметры, например ?api-version=….",
                  label_en="Chat API path",
                  help_en="Empty — protocol default. /v1/responses — relative to the site root; responses — relative to Base URL. Query parameters such as ?api-version=… are supported."),
            Field("api_key", "API-ключ", "password", placeholder="sk-…", required=False,
                  label_en="API key", help_en="Leave empty if the endpoint needs no key"),
            Field("api_keys", "Пул API-ключей", "textarea", required=False,
                  placeholder="sk-key-1\nsk-key-2\nsk-key-3",
                  help="По одному ключу в строке. Один эндпоинт сможет случайно выбирать ключ из пула.",
                  label_en="API key pool",
                  help_en="One key per line. The endpoint picks a key from the pool for each request."),
            Field("key_pool_mode", "Режим пула ключей", "select", required=False,
                  options=["random", "failover-on-match"], default="random",
                  help="random — случайный ключ. failover-on-match — отключить ключ и повторить запрос, если ошибка содержит одно из слов ниже.",
                  label_en="Key pool mode",
                  help_en="random — pick a random key. failover-on-match — disable the key and retry when the error contains one of the keywords below."),
            Field("key_error_keywords", "Ключевые слова критических ошибок", "textarea", required=False,
                  placeholder="API key's balance is too low\ninsufficient balance",
                  help="По одному фрагменту в строке, без учёта регистра. Не добавляйте сюда временные ошибки вроде Too many requests.",
                  label_en="Fatal error keywords",
                  help_en="One fragment per line, case-insensitive. Do not add transient errors such as Too many requests."),
            Field("headers", "Доп. заголовки (JSON)", "textarea", required=False,
                  placeholder='{"X-Custom": "value"}',
                  help="HTTP-заголовки, которые добавятся к каждому запросу к этому API. "
                       "Нужны, если сервис требует особые заголовки (например, x-api-key или HTTP-Referer). "
                       "Формат — JSON, в двойных кавычках.",
                  label_en="Extra headers (JSON)",
                  help_en="HTTP headers added to every request to this API. Needed when the service "
                          "requires special headers (for example x-api-key or HTTP-Referer). JSON with double quotes."),
            Field("models", "Модели (через запятую)", "textarea", required=False,
                  placeholder="gpt-5.6-sol, claude-opus-5",
                  label_en="Models (comma separated)",
                  help_en="Empty → the list is pulled from /models when the account is checked"),
            Field("image_models", "Модели генерации изображений", "textarea", required=False,
                  placeholder="gpt-image-2, flux-2-pro",
                  help="Необязательно. Укажите модели, которые работают через /images/generations. "
                       "Известные названия MemuAI распознаёт автоматически.",
                  label_en="Image generation models",
                  help_en="Optional. List the models served through /images/generations. "
                          "MemuAI recognises well-known names automatically."),

            Field("video_models", "Модели генерации видео", "textarea", required=False,
                  placeholder="sora-2, veo-3.1-generate-preview",
                  help="Необязательно. Модели, которые создают видео через /videos (как у OpenAI) или Veo в "
                       "Google AI Studio. Известные названия (sora, veo, kling…) MemuAI распознаёт сам.",
                  label_en="Video generation models",
                  help_en="Optional. Models that create video through /videos (OpenAI style) or Veo in "
                          "Google AI Studio. MemuAI recognises well-known names (sora, veo, kling…) itself."),
            Field("music_models", "Модели генерации музыки", "textarea", required=False,
                  placeholder="music_v2_5, music-3.0",
                  help="Точные ID через запятую. Эти модели появятся в Studio → Музыка и в /v1/models с capability music.",
                  label_en="Music generation models",
                  help_en="Exact IDs separated by commas. Available in Studio → Music and /v1/models with the music capability."),
            Field("music_config", "Музыкальный REST-адаптер (JSON)", "textarea", required=False,
                  placeholder='{"endpoint":"audio/music","auth":"bearer","audio_path":"data.0.url"}',
                  help="Выберите готовую схему ElevenLabs / MiniMax или настройте JSON по документации API. Ключ берётся из этого аккаунта.",
                  label_en="Music REST adapter (JSON)",
                  help_en="Choose an ElevenLabs / MiniMax schema or adapt JSON to the API documentation. Uses this account's key."),
        ]

    def validate_fields(self, fields: Dict[str, Any]) -> Optional[str]:
        name = str(fields.get("name", "")).strip().lower().replace(" ", "-")
        base = str(fields.get("base_url", "")).strip().rstrip("/")
        if not name:
            return "Укажите название (префикс)"
        from . import builtin_ids
        if name in builtin_ids():
            return f"Префикс «{name}» зарезервирован встроенным провайдером"
        if not base.startswith(("http://", "https://")):
            return "Base URL должен начинаться с http(s)://"
        if str(fields.get("api_protocol", "auto") or "auto") not in ("auto", "chat", "responses"):
            return "Неизвестный протокол чата / Unknown chat protocol"
        try:
            self.chat_url(type("Connection", (), {"fields": fields})())
        except ValueError as exc:
            return str(exc)
        if str(fields.get("key_pool_mode", "random") or "random") not in (
                "random", "failover-on-match"):
            return "Неизвестный режим пула API-ключей"
        if fields.get("music_config"):
            from ..music_runtime import parse_config
            try:
                parse_config(fields)
            except ValueError as exc:
                return str(exc)
        elif fields.get("music_models"):
            return "Выберите музыкальную схему / Choose a music schema"
        return None

    @staticmethod
    def base_of(account) -> str:
        return str(account.fields.get("base_url", "")).strip().rstrip("/")

    @staticmethod
    def chat_protocol(account) -> str:
        protocol = str(account.fields.get("api_protocol", "auto") or "auto").strip()
        if protocol == "auto":
            path = str(account.fields.get("chat_endpoint", "") or "").strip()
            return "responses" if urlsplit(path).path.rstrip("/").endswith("/responses") \
                or urlsplit(path).path.rstrip("/") == "responses" else "chat"
        if protocol not in ("chat", "responses"):
            raise ValueError("Неизвестный протокол чата / Unknown chat protocol")
        return protocol

    @classmethod
    def chat_url(cls, account) -> str:
        """User-owned route, always on the configured origin (never leak its key)."""
        base = cls.base_of(account)
        endpoint = str(account.fields.get("chat_endpoint", "") or "").strip()
        if not endpoint:
            endpoint = "responses" if cls.chat_protocol(account) == "responses" else "chat/completions"
        parsed = urlsplit(endpoint)
        if parsed.scheme or parsed.netloc or endpoint.startswith("//") or "\\" in endpoint \
                or parsed.fragment or any(ord(char) < 32 or ord(char) == 127 for char in endpoint) \
                or any(part in (".", "..") for part in parsed.path.split("/")):
            raise ValueError("Укажите путь API без другого хоста, #, обратных слешей или .. / Enter an API path on the same host")
        if len(endpoint) > 4096:
            raise ValueError("Путь API слишком длинный / API path is too long")
        if endpoint.startswith("/"):
            origin = urlsplit(base)
            return urlunsplit((origin.scheme, origin.netloc, parsed.path, parsed.query, ""))
        return f"{base}/{endpoint}"

    def proxy_for(self, account) -> str:

        if not account.proxy and http.is_loopback(self.base_of(account)):
            return ""
        return super().proxy_for(account)

    @staticmethod
    def extra_headers(account) -> Dict[str, str]:
        raw = str(account.fields.get("headers", "") or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return {str(k): str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _key_hash(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def _all_keys(self, account) -> List[str]:

        raw = str(account.fields.get("api_key", "") or "") + "\n" + str(account.fields.get("api_keys", "") or "")
        values = [part.strip() for part in re.split(r"[\r\n;]+", raw) if part.strip()]
        return list(dict.fromkeys(values))

    def _key_candidates(self, account) -> List[str]:
        keys = self._all_keys(account)
        if not keys:
            return [""]
        disabled = {str(value) for value in account.extra.get("disabled_api_key_hashes", [])}
        active = [key for key in keys if self._key_hash(key) not in disabled]
        if not active:
            raise ProviderAuthError(
                "Every API key of this connection is disabled by the rotation rules. "
                "Change the pool or the error keywords.")
        random.shuffle(active)
        active.sort(key=self._key_cooling)
        mode = str(account.fields.get("key_pool_mode", "random") or "random")
        return active if mode == "failover-on-match" else active[:1]

    _rate_limited: Dict[str, float] = {}

    def _key_cooling(self, key: str) -> bool:
        until = self._rate_limited.get(self._key_hash(key)) if key else None
        return bool(until and until > time.monotonic())

    def _note_rate_limited(self, key: str, retry_after: Optional[float]) -> None:
        if not key:
            return
        wait = retry_after if retry_after and retry_after > 0 else 60.0
        self._rate_limited[self._key_hash(key)] = time.monotonic() + min(max(wait, 5.0), 900.0)

    def _rate_limit_spares(self, account) -> List[str]:
        """Other active keys of the pool, shuffled — fallbacks after an HTTP 429."""
        keys = self._all_keys(account)
        disabled = {str(value) for value in account.extra.get("disabled_api_key_hashes", [])}
        active = [key for key in keys if self._key_hash(key) not in disabled]
        random.shuffle(active)
        active.sort(key=self._key_cooling)
        return active

    def _matches_key_failure(self, account, message: str) -> bool:
        if str(account.fields.get("key_pool_mode", "random") or "random") != "failover-on-match":
            return False
        keywords = [part.strip().casefold() for part in re.split(
            r"[\r\n;]+", str(account.fields.get("key_error_keywords", "") or "")) if part.strip()]
        haystack = str(message or "").casefold()
        return bool(keywords) and any(keyword in haystack for keyword in keywords)

    def _disable_key(self, account, key: str) -> None:
        if not key:
            return
        hashes = {str(value) for value in account.extra.get("disabled_api_key_hashes", [])}
        hashes.add(self._key_hash(key))
        account.extra["disabled_api_key_hashes"] = sorted(hashes)
        from ..state import state
        state.save_accounts()

    def _headers(self, account, accept: str = "application/json",
                 api_key: Optional[str] = None) -> Dict[str, str]:
        h = {"Accept": accept, "Content-Type": "application/json"}
        key = self._key_candidates(account)[0] if api_key is None else api_key
        extra = self.extra_headers(account)

        templated = any("{api_key}" in value for value in extra.values())
        if templated:
            extra = {name: value.replace("{api_key}", key or "") for name, value in extra.items()}
            extra = {name: value for name, value in extra.items() if value.strip() and value.strip() != "Bearer"}
        if key and not templated:
            h["Authorization"] = f"Bearer {key}"
        h.update(extra)
        return h

    @staticmethod
    def _with_prompt_cache(body: Dict[str, Any], model: str,
                           enabled: bool, base: str = "") -> Tuple[Dict[str, Any], bool]:
        """Add only cache hints understood by the apparent API family (Round 40: app/prompt_cache.py —
        Claude ≤4 breakpoints, Gemini on OpenRouter one, OpenAI prompt_cache_key, the rest implicit).
        Unsupported compatible APIs are retried once without these optional fields by ``chat_stream``."""
        from .. import prompt_cache
        return prompt_cache.apply(body, model, base=base, enabled=enabled)

    async def chat_stream(self, account, model: str, messages: List[Dict[str, Any]],
                          params: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
        base = self.base_of(account)
        if not base:
            raise ProviderServerError("The account has no base_url")
        try:
            protocol = self.chat_protocol(account)
            url = self.chat_url(account)
        except ValueError as exc:
            raise ProviderServerError(str(exc), 400) from exc
        body: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        for src, dst in (("temperature", "temperature"), ("max_tokens", "max_tokens"),
                         ("top_p", "top_p"), ("stream_options", "stream_options"),
                         ("tools", "tools"), ("tool_choice", "tool_choice"),
                         ("parallel_tool_calls", "parallel_tool_calls"),

                         ("top_k", "top_k"), ("min_p", "min_p"), ("top_a", "top_a"),
                         ("frequency_penalty", "frequency_penalty"), ("presence_penalty", "presence_penalty"),
                         ("repetition_penalty", "repetition_penalty"), ("seed", "seed"), ("stop", "stop"),
                         ("response_format", "response_format")):
            if params.get(src) is not None and params.get(src) != []:
                body[dst] = params[src]

        reasoning_info: Dict[str, Any] = {"fields": []}
        try:
            if isinstance(params.get("_reasoning"), dict) and params.get("_reasoning_level"):
                reasoning_info = reasoning_levels.apply_openai_compatible(
                    body, params["_reasoning"], str(params["_reasoning_level"]), base)
        except Exception:
            reasoning_info = {"fields": []}
        cached_body, cache_hint = self._with_prompt_cache(
            body, model, bool(params.get("prompt_caching", True)), base)

        usage_requested = protocol == "chat" and "stream_options" not in body
        if usage_requested:
            cached_body = {**cached_body, "stream_options": {"include_usage": True}}

        reasoning_plain = reasoning_levels.strip_fields(body, reasoning_info["fields"]) \
            if reasoning_info.get("fields") else None
        candidates = self._key_candidates(account)

        rate_limit_spare = [key for key in self._rate_limit_spares(account) if key not in candidates]
        resp = None
        selected_key = ""
        usage_sent = False
        retry_after: Optional[float] = None
        for key_index, selected_key in enumerate(candidates):
            head = ""
            request_bodies = [cached_body, body] if (cache_hint or usage_requested) else [body]
            if reasoning_plain is not None:
                request_bodies.append(reasoning_plain)
            reasoning_refused = False
            for request_index, request_body in enumerate(request_bodies):
                outgoing_body = request_body
                if protocol == "responses":
                    from .responses import request_body as responses_body
                    outgoing_body = responses_body(request_body)
                try:
                    session = await http.get_session(self.proxy_for(account),
                                                     impersonate=not base.startswith("http://"), url=base)
                    resp = await session.post(url, headers=self._headers(account, api_key=selected_key),
                                              json=outgoing_body, stream=True,
                                              timeout=config_store.settings.request_timeout)
                except Exception as e:
                    raise ProviderNetworkError(f"Network error: {type(e).__name__}: {e}")

                status = resp.status_code
                if status == 200:
                    usage_sent = usage_requested and request_index == 0
                    if reasoning_refused and request_body is reasoning_plain:
                        try:
                            reasoning_levels.learn_rejection(
                                str(params.get("_model_id") or model), str(reasoning_info.get("level") or ""))
                        except Exception:
                            pass
                    break
                retry_after = retry_after_seconds(getattr(resp, "headers", None))
                if status == 429:
                    try:
                        from .. import quota_planner
                        quota_planner.observe_headers(account.id, getattr(resp, "headers", None), selected_key)
                    except Exception:
                        pass
                head = ""
                try:
                    async for chunk in resp.aiter_content():
                        head += chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else str(chunk)
                        if len(head) > 3000:
                            break
                except Exception:
                    pass
                finally:
                    try:
                        await resp.aclose()
                    except Exception:
                        pass
                if request_index + 1 < len(request_bodies) and status in (400, 415, 422):
                    reasoning_refused = request_body is body and reasoning_plain is not None
                    continue
                if reasoning_plain is not None and request_body is body \
                        and reasoning_levels.looks_like_rejection(status, head):

                    reasoning_refused = True
                    continue
                break
            if resp is not None and resp.status_code == 200:
                break
            if self._matches_key_failure(account, head) and selected_key:
                self._disable_key(account, selected_key)
                if key_index + 1 < len(candidates):
                    continue
            if status == 429:
                self._note_rate_limited(selected_key, retry_after)

                if key_index + 1 < len(candidates):
                    continue
                if rate_limit_spare:
                    candidates.append(rate_limit_spare.pop(0))
                    continue
            await self._raise_for_status(status, head, messages_have_images(messages), retry_after=retry_after)
        if resp is None:
            raise ProviderServerError("Could not pick an API key for the request")
        try:
            from .. import quota_planner
            quota_planner.observe_headers(account.id, getattr(resp, "headers", None), selected_key)
        except Exception:
            pass

        async def raw_response_lines() -> AsyncIterator[str]:
            line_stream = http.aiter_lines_str(
                resp, idle_timeout=config_store.settings.stream_idle_timeout)
            content_type = str((getattr(resp, "headers", {}) or {}).get("content-type", "")).lower()
            if "application/json" not in content_type:
                async for item in line_stream:
                    yield item
                return

            parts: List[str] = []
            size = 0
            async for item in line_stream:
                text = str(item or "")
                size += len(text.encode("utf-8", errors="replace"))
                if size > 4 * 1024 * 1024:
                    raise ProviderServerError("The upstream JSON response exceeds 4 MB")
                parts.append(text)
            if parts:
                yield "\n".join(parts)

        async def response_lines() -> AsyncIterator[str]:
            if protocol != "responses":
                async for line in raw_response_lines():
                    yield line
                return
            from .responses import StreamAdapter
            adapter = StreamAdapter()
            pending: List[str] = []
            pending_size = 0
            async for line in raw_response_lines():
                line = str(line or "").strip()
                if line.startswith("data:"):
                    line = line[5:].strip()
                if not line:
                    pending.clear()
                    pending_size = 0
                    continue
                if line.startswith(("event:", "id:", "retry:", ":")) or line == "[DONE]":
                    continue
                pending.append(line)
                pending_size += len(line.encode("utf-8", errors="replace"))
                if pending_size > 4 * 1024 * 1024:
                    raise ProviderServerError("The upstream JSON response exceeds 4 MB")
                try:
                    obj = json.loads("\n".join(pending))
                except (TypeError, ValueError):
                    continue
                pending.clear()
                pending_size = 0
                if isinstance(obj, dict):
                    for converted in adapter.feed(obj):
                        yield json.dumps(converted, ensure_ascii=False)
                if adapter.terminal:
                    return
            adapter.require_terminal()

        saw_output = False

        tool_slots = {"next": 0, "last": 0}
        done_reason = ""
        reasoning_seen = False
        lines = response_lines().__aiter__()
        try:
            while True:
                try:

                    line = await (asyncio.wait_for(lines.__anext__(), 3.0) if done_reason else lines.__anext__())
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    if done_reason:
                        break
                    raise
                line = (line or "").strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if not line:
                    continue
                if line == "[DONE]":
                    break
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("error"):
                    err = obj["error"]
                    msg = err.get("message") if isinstance(err, dict) else str(err)
                    err_type = str(err.get("type") or err.get("code") or "").lower() \
                        if isinstance(err, dict) else ""
                    raw_status = err.get("status") if isinstance(err, dict) else None
                    if raw_status is None and isinstance(err, dict) and isinstance(err.get("code"), int):
                        raw_status = err["code"]
                    try:
                        err_status = int(raw_status)
                    except (TypeError, ValueError):
                        err_status = 502
                    detail = f"Upstream error: {msg}"
                    if self._matches_key_failure(account, detail) and selected_key:
                        self._disable_key(account, selected_key)
                        if not saw_output and len(self._key_candidates(account)) > 0:
                            async for retry_event in self.chat_stream(account, model, messages, params):
                                yield retry_event
                            return
                    if err_status in (401, 403) or "auth" in err_type or "invalid_api_key" in err_type:
                        raise ProviderAuthError(detail)
                    if err_status == 429 or "rate_limit" in err_type:
                        raise ProviderRateLimitError(detail)
                    if err_status == 402 or "quota" in err_type:
                        raise ProviderQuotaError(detail)
                    raise ProviderServerError(detail, err_status if 400 <= err_status <= 599 else 502)
                counted = obj.get("usage")
                if isinstance(counted, dict) and (counted.get("prompt_tokens") or counted.get("completion_tokens")):

                    usage_event = {"type": "usage", "prompt_tokens": counted.get("prompt_tokens"),
                                   "completion_tokens": counted.get("completion_tokens")}
                    details = counted.get("completion_tokens_details")
                    if isinstance(details, dict) and isinstance(details.get("reasoning_tokens"), int):

                        usage_event["reasoning_tokens"] = details["reasoning_tokens"]

                    prompt_details = counted.get("prompt_tokens_details")
                    cached = (prompt_details.get("cached_tokens") if isinstance(prompt_details, dict) else None)
                    if not isinstance(cached, int):
                        cached = counted.get("prompt_cache_hit_tokens")
                    if isinstance(cached, int) and cached > 0:
                        usage_event["cached_tokens"] = cached
                    written = prompt_details.get("cache_write_tokens") if isinstance(prompt_details, dict) else None
                    if isinstance(written, int) and not isinstance(written, bool) and written > 0:
                        usage_event["cache_write_tokens"] = written

                    if isinstance(counted.get("cost"), (int, float)) and not isinstance(counted.get("cost"), bool) \
                            and counted["cost"] >= 0:
                        usage_event["cost"] = float(counted["cost"])
                    yield usage_event
                    if done_reason:
                        break
                if done_reason:
                    continue
                finished = False
                finish_reason = ""
                for choice in obj.get("choices") or []:
                    if not isinstance(choice, dict):
                        continue

                    delta = choice.get("delta") or choice.get("message") or {}
                    if not isinstance(delta, dict):
                        delta = {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        saw_output = True
                        if not reasoning_seen:
                            reasoning_seen = True

                            prof = params.get("_reasoning") if isinstance(params.get("_reasoning"), dict) else {}
                            if prof.get("source") == "guess" and prof.get("kind") == "none":
                                try:
                                    reasoning_levels.learn_reasons(str(params.get("_model_id") or model))
                                except Exception:
                                    pass
                        yield {"type": "reasoning", "text": str(reasoning)}
                    content = delta.get("content")
                    if isinstance(content, list):
                        content = "".join(
                            str(part.get("text") or "") for part in content
                            if isinstance(part, dict) and part.get("type") in ("text", "output_text")
                        )
                    if content:
                        saw_output = True
                        yield {"type": "text", "text": str(content)}
                    for tool_call in delta.get("tool_calls") or []:
                        if not isinstance(tool_call, dict):
                            continue
                        function = tool_call.get("function") or {}
                        if not isinstance(function, dict):
                            continue
                        index = tool_call.get("index")
                        if not isinstance(index, int):
                            if tool_call.get("id") is not None or function.get("name"):
                                index = tool_slots["next"]
                            else:
                                index = tool_slots["last"]
                        tool_slots["last"] = index
                        tool_slots["next"] = max(tool_slots["next"], index + 1)
                        event = {"type": "tool_call", "index": index,
                                 "id": tool_call.get("id"), "name": function.get("name"),
                                 "arguments": function.get("arguments")}
                        if any(event.get(key) is not None for key in ("id", "name", "arguments")):
                            saw_output = True
                            yield event
                    if choice.get("finish_reason") is not None:
                        finished = True
                        finish_reason = str(choice.get("finish_reason") or "stop")
                if finished:
                    if finish_reason != "stop":
                        yield {"type": "finish", "reason": finish_reason}
                    if not usage_sent or isinstance(counted, dict):
                        return
                    done_reason = finish_reason
        except ProviderError:
            raise
        except asyncio.TimeoutError:
            raise ProviderNetworkError(
                f"The upstream sent no data for over {config_store.settings.stream_idle_timeout} s")
        except Exception as e:
            raise ProviderServerError(f"The upstream stream broke off: {type(e).__name__}: {e}")
        finally:

            await http.close_response(resp)

    async def _raise_for_status(self, status: int, head: str, had_images: bool = False,
                                *, retry_after: Optional[float] = None) -> None:
        if status == 403 and _MODEL_DENIED_RE.search(_upstream_detail(head) or head[:400]):

            from .base import ProviderModelUnavailableError
            raise ProviderModelUnavailableError(
                f"The key has no access to this model (HTTP 403): {_upstream_detail(head)}",
                message_ru=f"У ключа нет доступа к этой модели (HTTP 403): {_upstream_detail(head)}")
        if status in (401, 403):

            raise ProviderAuthError(f"The API key was rejected (HTTP {status}): {_upstream_detail(head)}")

        detail = _upstream_detail(head)
        if status == 429:
            raise ProviderRateLimitError("Upstream rate limit (HTTP 429)" + (f": {detail}" if detail else ""),
                                         retry_after=retry_after)
        if status == 402:
            raise ProviderQuotaError("The upstream balance is used up (HTTP 402)" + (f": {detail}" if detail else ""))
        if had_images and status in (400, 415, 422):
            raise ProviderServerError(
                f"The model or API rejected the image (HTTP {status}). "
                "Pick a vision model or remove the attachment. " + head[:180], status)
        raise ProviderServerError(f"Upstream error (HTTP {status}): {_upstream_detail(head) or head[:150]}", status)

    async def generate_images(self, account, prompt: str, model: str,
                              aspect: str = "auto", images: Optional[List[str]] = None) -> List[str]:
        """OpenAI-compatible Images API. Reference-image editing is provider-specific."""
        if images:
            raise ProviderServerError(
                "Редактирование по референсу не стандартизировано для собственного API. "
                "Отправьте текстовый промпт без вложения или используйте Genspark AI Image.", 400)
        body: Dict[str, Any] = {"model": model, "prompt": prompt, "n": 1}
        sizes = {"1:1": "1024x1024", "16:9": "1536x1024", "9:16": "1024x1536",
                 "4:3": "1536x1024", "3:4": "1024x1536"}
        if aspect in sizes:
            body["size"] = sizes[aspect]
        try:
            session = await http.get_session(self.proxy_for(account),
                                             impersonate=not self.base_of(account).startswith("http://"),
                                             url=self.base_of(account))
            resp = await session.post(f"{self.base_of(account)}/images/generations",
                                      headers=self._headers(account), json=body,
                                      timeout=config_store.settings.request_timeout)
        except Exception as e:
            raise ProviderNetworkError(f"Network error during generation: {type(e).__name__}: {e}")
        if resp.status_code != 200:
            await self._raise_for_status(resp.status_code, str(resp.text or ""),
                                         retry_after=retry_after_seconds(getattr(resp, "headers", None)))
        try:
            payload = resp.json() or {}
            urls: List[str] = []
            for item in payload.get("data") or []:
                if not isinstance(item, dict):
                    continue
                if item.get("url"):
                    urls.append(str(item["url"]))
                elif item.get("b64_json"):
                    urls.append("data:image/png;base64," + str(item["b64_json"]))
            if not urls:
                raise ProviderServerError(
                    "The API answered successfully but returned no data[].url or data[].b64_json.")
            return urls
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderServerError(f"Could not parse the generation response: {type(e).__name__}: {e}")

    async def check(self, account) -> Tuple[Optional[bool], str]:
        base = self.base_of(account)
        try:
            session = await http.get_session(self.proxy_for(account),
                                             impersonate=not self.base_of(account).startswith("http://"),
                                             url=self.base_of(account))
            resp = await session.get(f"{base}/models", headers=self._headers(account),
                                     timeout=30)
        except Exception as e:
            return None, f"сеть недоступна: {http.network_error_text(e)}"
        if resp.status_code in (401, 403):
            return False, f"ключ отклонён (HTTP {resp.status_code})"
        if resp.status_code != 200:

            if account.fields.get("models") and (account.fields.get("chat_endpoint")
                                                  or self.chat_protocol(account) == "responses"):
                verdict = await self._probe_key(account, session, base, [])
                if verdict is not None:
                    return verdict
            return None, f"HTTP {resp.status_code}: адрес отвечает, но список моделей /models недоступен"
        ids: List[str] = []
        parsed = True
        try:
            data = resp.json()
            ids = [m.get("id") for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")]
        except Exception:
            parsed = False

        verdict = await self._probe_key(account, session, base, ids)
        if verdict is not None:
            return verdict
        if not parsed:
            return True, "подключено (ответ /models не разобран)"
        return True, f"подключено · моделей: {len(ids)}"

    async def _probe_key(self, account, session, base: str, listed: List[str]):
        """Cheap key check: an invalid chat request (no messages, 1 token).

        Gateways authenticate before validating the body, so a bad key answers
        401/403 while a good one answers 400/422 — no tokens are spent. Returns
        ``(False, msg)`` for a rejected key, ``(True, msg)`` for a key at its
        limit, ``None`` when the key is accepted or the probe is inconclusive."""
        if not self._all_keys(account) and not (account.fields.get("chat_endpoint")
                                                or self.chat_protocol(account) == "responses"):
            return None
        declared = [m.strip() for m in re.split(r"[,\n;]", str(account.fields.get("models", "") or "")) if m.strip()]
        model = (declared or listed or ["gpt-4o-mini"])[0]
        try:
            protocol = self.chat_protocol(account)
            payload = {"model": model, "messages": [], "max_tokens": 1}
            if protocol == "responses":

                payload = {"model": model, "input": None, "max_output_tokens": 0, "store": False}
            probe = await session.post(self.chat_url(account), headers=self._headers(account),
                                       json=payload, timeout=20)
        except Exception:
            return None
        if probe.status_code in (401, 403):
            return False, f"ключ отклонён (HTTP {probe.status_code})"
        if probe.status_code == 402:
            return True, "подключено · ключ принят, но на балансе нет средств (HTTP 402)"
        if probe.status_code == 429:
            return True, "подключено · ключ принят, но сейчас исчерпан лимит запросов (HTTP 429)"
        if probe.status_code == 200:
            return True, "подключено · API чата отвечает"
        if probe.status_code in (400, 422) and account.fields.get("models"):
            return True, "подключено · модели заданы вручную, API чата доступен"
        return None

    async def dynamic_models(self, account) -> Optional[List[str]]:
        from ..music_runtime import model_names
        explicit = [value.strip() for value in re.split(r"[,\n;]", str(account.fields.get("models", "") or "")) if value.strip()]
        music = model_names(account.fields)

        media = [value.strip() for field in ("image_models", "video_models")
                 for value in re.split(r"[,\n;]", str(account.fields.get(field, "") or "")) if value.strip()]
        if explicit or music:
            return list(dict.fromkeys(explicit + media + music))
        base = self.base_of(account)
        try:
            session = await http.get_session(self.proxy_for(account),
                                             impersonate=not self.base_of(account).startswith("http://"),
                                             url=self.base_of(account))
            resp = await session.get(f"{base}/models", headers=self._headers(account),
                                     timeout=30)
            if resp.status_code != 200:
                return None
            data = resp.json()
            rows = data.get("data") or data.get("models") or []
            try:

                from .. import model_info
                prefix = str(account.fields.get("name", "")).strip().lower().replace(" ", "-")
                model_info.record_rows(prefix or "custom", rows, source="api:models")

                from .. import model_capabilities
                model_capabilities.record_raw(prefix or "custom", rows, source="api:models")
            except Exception:
                pass
            listed = [m.get("id") for m in rows if isinstance(m, dict) and m.get("id")]
            return list(dict.fromkeys(listed + media))
        except Exception:
            return None

def _upstream_detail(head: str, limit: int = 300) -> str:
    """The message an upstream error body carries (``{"error":{"message":…}}``), else its first characters.

    Round 32: the body used to be cut at 150 characters before parsing, so a longer JSON error reached the
    user as a broken JSON fragment instead of its message."""
    from .base import readable_provider_error
    text = str(head or "").strip()
    if not text:
        return ""
    readable = readable_provider_error(text[:4000])
    return " ".join(readable.split())[:limit]
