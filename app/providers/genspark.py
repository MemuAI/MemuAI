"""Провайдер Genspark: /api/agent/ask_proxy (+ резерв /api/copilot/ask).

Умеет продолжать «проект» Genspark по хвосту истории клиента (режимы
auto/server/stateless), пересылает reasoning, умеет включать веб-поиск и
находить новые модели сканом бандла.
"""
from __future__ import annotations

import asyncio
import json
import hashlib
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

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
    normalize_messages,
    transcript_prompt,
    spawn_background,
)
from ..config import config_store
from .gs import catalog as gs_catalog
from .gs.client import (
    GensparkAuthError,
    GensparkClient,
    GensparkCloudflareError,
    GensparkContentFilterError,
    GensparkError,
    GensparkModelError,
    GensparkFreeLimitError,
    GensparkNetworkError,
    GensparkRateLimitError,
    GensparkServerError,
    looks_like_cloudflare,
)
from .gs.stream import iter_upstream_events
from . import http

GS_SEED_MODELS = [
    ("gpt-5.4", "GPT-5.4 · 3x"), ("gpt-5.5", "GPT-5.5 · 5x"),
    ("gpt-5.6-sol", "GPT-5.6 Sol · 4x · id to verify"),
    ("gpt-5.6-terra", "GPT-5.6 Terra · 2x · id to verify"),
    ("gpt-5.6-luna", "GPT-5.6 Luna · 0.2x · id to verify"),
    ("gpt-5.4-mini", "GPT-5.4 Mini · 1x"), ("gpt-5.4-nano", "GPT-5.4 Nano · 0.2x"),
    ("gpt-5.2-pro", "GPT-5.2 Pro · 15x"), ("gpt-5.4-pro", "GPT-5.4 Pro · 30x"),
    ("gpt-5.5-pro", "GPT-5.5 Pro · 30x · id to verify"),
    ("claude-sonnet-5", "Claude Sonnet 5 · 2x · id to verify"),
    ("claude-sonnet-4-6", "Claude Sonnet 4.6 · 3x"),
    ("claude-opus-5", "Claude Opus 5 · 5x · id to verify"),
    ("claude-opus-4-8", "Claude Opus 4.8 · 5x · id to verify"),
    ("claude-opus-4-7", "Claude Opus 4.7 · 5x"),
    ("claude-opus-4-6", "Claude Opus 4.6 · 5x"),
    ("claude-4-5-haiku", "Claude Haiku 4.5 · 1x"),
    ("gemini-3-flash-preview", "Gemini 3 Flash Preview · 0.5x"),
    ("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview · 2x"),
    ("gemini-3.1-flash-lite", "Gemini 3.1 Flash Lite · 0.3x · id to verify"),
    ("gemini-3.5-flash", "Gemini 3.5 Flash · 2x · id to verify"),
    ("gemini-3.6-flash", "Gemini 3.6 Flash · 0.75x · id to verify"),
    ("gemini-3.7-flash", "Gemini 3.7 Flash · 0.75x · id to verify"),
    ("deepseek-v4-pro", "DeepSeek V4 Pro · 2x · id to verify"),
    ("deepseek-v4-flash", "DeepSeek V4 Flash · 0.44x · id to verify"),
    ("deepseek-v4-pro-0813", "DeepSeek V4 Pro 0813 · 1x · id to verify"),
    ("minimax-m2.7", "Minimax M2.7 · 0.3x · id to verify"),
    ("minimax-m3", "Minimax M3 · 0.3x · id to verify"),
    ("kimi-k3", "Kimi K3 · 3x · id to verify"),
    ("solar-pro-4", "Solar Pro 4 · 0.3x · id to verify"),
    ("nemotron-3-ultra", "Nemotron 3 Ultra · 0.6x · id to verify"),
    ("glm-5.2", "GLM-5.2 · 1x · id to verify"),
    ("glm-5.3", "GLM-5.3 · 1x · id to verify"),
    ("grok-4.5", "Grok 4.5 · 2x · id to verify"),
    ("grok-4.6", "Grok 4.6 · id to verify"),
    ("mixture-of-agents", "гибрид: Genspark смешивает ответы"),
]


def is_chat_model(model_id: str) -> bool:
    """Reject UI-only Genspark modes that cannot be selected in AI Chat."""
    normalized = str(model_id or "").strip().lower().replace("_", "-")
    return bool(normalized) and "fable-5" not in normalized

# Genspark's current AI Image UI expands ``auto`` into real upstream model
# identifiers.  Sending a literal ``auto`` model creates only the empty
# ``moa_image_result`` message shell and never starts an image task.
GS_IMAGE_AUTO_MODELS = [
    "fal-ai/nano-banana",
    "gpt-image-1",
    "fal-ai/flux-2",
    "fal-ai/bytedance/seedream/v5/lite",
]


# ------------------------------ контекст ------------------------------

@dataclass
class Conv:
    project_id: str
    account_id: str
    model: str
    tail_len: int
    tail_hash: str
    last_used: float


class ConversationStore:
    MAX_PER_KEY = 24
    TTL = 48 * 3600

    def __init__(self) -> None:
        self._items: Dict[str, List[Conv]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def hash_messages(messages: List[Dict[str, Any]]) -> str:
        norm = []

        def attachment_digest(value: Any) -> str:
            digest = hashlib.sha256()

            def feed(item: Any) -> None:
                if isinstance(item, dict):
                    digest.update(b"{")
                    for key in sorted(item, key=lambda candidate: str(candidate)):
                        feed(str(key))
                        feed(item[key])
                    digest.update(b"}")
                elif isinstance(item, (list, tuple)):
                    digest.update(b"[")
                    for child in item:
                        feed(child)
                    digest.update(b"]")
                else:
                    digest.update(type(item).__name__.encode("ascii", errors="ignore"))
                    digest.update(b":")
                    digest.update(str(item).encode("utf-8", errors="replace"))
                    digest.update(b";")

            feed(value)
            return digest.hexdigest()

        for m in messages:
            content = m.get("content")
            c = content_to_text(content)
            attachments = []
            if isinstance(content, list):
                attachments = [
                    attachment_digest(part) for part in content
                    if not (isinstance(part, dict) and
                            part.get("type") in ("text", "input_text", "output_text"))
                ]
            norm.append(f"{m.get('role', '')}:{c}:attachments={','.join(attachments)}")
        return hashlib.sha1("\n".join(norm).encode("utf-8")).hexdigest()[:20]

    def _prune(self) -> None:
        cutoff = time.time() - self.TTL
        for key in list(self._items.keys()):
            self._items[key] = [c for c in self._items[key] if c.last_used > cutoff]
            if not self._items[key]:
                del self._items[key]

    async def match(self, model: str, prefix_msgs: List[Dict[str, Any]], account_id: str) -> Optional[Conv]:
        if not prefix_msgs:
            return None
        async with self._lock:
            self._prune()
            best = None
            for conv in self._items.get(model, []):
                if conv.account_id != account_id or len(prefix_msgs) < conv.tail_len:
                    continue
                if self.hash_messages(prefix_msgs[-conv.tail_len:]) == conv.tail_hash:
                    if best is None or conv.last_used > best.last_used:
                        best = conv
            return best

    async def find_latest(self, model: str, account_id: str) -> Optional[Conv]:
        async with self._lock:
            self._prune()
            lst = [c for c in self._items.get(model, []) if c.account_id == account_id]
            return max(lst, key=lambda c: c.last_used) if lst else None

    async def add(self, conv: Conv) -> None:
        async with self._lock:
            self._prune()
            lst = self._items.setdefault(conv.model, [])
            lst[:] = [c for c in lst if c.project_id != conv.project_id]
            lst.append(conv)
            lst.sort(key=lambda c: c.last_used)
            while len(lst) > self.MAX_PER_KEY:
                lst.pop(0)


conversations = ConversationStore()


def _history_needed(messages: List[Dict[str, Any]]) -> bool:
    return len([m for m in messages if m.get("role") == "user"]) > 1


# ------------------------------ провайдер ------------------------------


def _int_cfg(cfg: Dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    """Числовая настройка из панели. Пустое поле или 0 — значение по умолчанию.

    Раньше int("") валил каждый запрос, а 0 давал мгновенный таймаут.
    """
    try:
        value = int(float(cfg.get(key) or 0))
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return int(default)
    return max(low, min(high, value))

class GensparkProvider(Provider):
    id = "genspark"
    native_search = True

    def settings_fields(self) -> List[Field]:
        return [
            Field("chat_backend", "Бэкенд чата", "select", required=False, default="agent",
                  options=["agent", "copilot", "auto"], label_en="Chat backend"),
            Field("context_mode", "Режим контекста", "select", required=False, default="auto",
                  options=["auto", "server", "stateless"], label_en="Context mode"),
            Field("reasoning_output", "Вывод «мышления»", "select", required=False,
                  default="reasoning_content",
                  options=["reasoning_content", "think_tags", "hidden"],
                  label_en="Reasoning output"),
            Field("auto_delete_chats", "Удалять временные чаты", "select", required=False,
                  default="true", options=["true", "false"],
                  label_en="Delete temporary chats"),
            Field("stream_idle_timeout", "Таймаут тишины, с", "number", required=False,
                  default="300", label_en="Stream idle timeout, s"),
        ]
    title = "Genspark"
    icon = "✦"
    title_en = "Genspark"
    description = "AI-воркспейс с топовыми моделями. Кука session_id из браузера."
    description_en = "AI workspace with frontier models. Uses the session_id cookie from your browser."

    def __init__(self) -> None:
        cfg = self._cfg()
        self.client = GensparkClient(
            api_base=cfg.get("api_base", "https://www.genspark.ai"),
            impersonate=cfg.get("impersonate", "chrome136"),
            verify_ssl=self._bool(cfg.get("verify_ssl", True)),
            user_agent=cfg.get("user_agent", ""),
        )
        self._prefer_backend = ""

    # ---- служебное ----

    def _cfg(self) -> Dict[str, Any]:
        return config_store.provider_cfg("genspark")

    def _bool(self, v: Any) -> bool:
        if isinstance(v, str):
            return v.lower() in ("1", "true", "yes", "on")
        return bool(v)

    async def reconfigure(self) -> None:
        cfg = self._cfg()
        await self.client.reconfigure(
            api_base=cfg.get("api_base", "https://www.genspark.ai"),
            impersonate=cfg.get("impersonate", "chrome136"),
            verify_ssl=self._bool(cfg.get("verify_ssl", True)),
            user_agent=cfg.get("user_agent", ""),
        )

    async def close(self) -> None:
        await self.client.close()

    # ---- интерфейс провайдера ----

    def account_fields(self) -> List[Field]:
        return [
            Field("cookie", "Кука session_id", "textarea",
                  placeholder="session_id=2|1:aBcDeFgH… или голый id",
                  help="F12 на genspark.ai → Application → Cookies → session_id",
                  label_en="session_id cookie",
                  placeholder_en="session_id=2|1:aBcDeFgH… or the bare id",
                  help_en="F12 on genspark.ai → Application → Cookies → session_id"),
        ]

    def seed_models(self) -> List[Tuple[str, str]]:
        return [(model, note) for model, note in GS_SEED_MODELS if is_chat_model(model)]

    async def check(self, account) -> Tuple[Optional[bool], str]:
        cookie = self._cookie(account)
        ok, message = await self.client.is_login(cookie, proxy=self.proxy_for(account))
        if ok is True:
            info = await self.client.user_info(cookie, proxy=self.proxy_for(account))
            if info.get("email"):
                message = f"проверена · {info['email']}"
        return ok, message

    async def usage(self, account) -> Optional[Dict[str, Any]]:
        info = await self.client.user_info(self._cookie(account), proxy=self.proxy_for(account))
        return info or None

    async def dynamic_models(self, account) -> Optional[List[str]]:
        """Chat-модели из бандла (по куке). Image/video отдаются отдельно."""
        try:
            res = await gs_catalog.discover_chat_models(
                self.client, self._cookie(account), proxy=self.proxy_for(account))
            return [model for model in (res.get("ids") or []) if is_chat_model(model)]
        except GensparkError:
            return None

    # ---- чат ----

    async def chat_stream(self, account, model: str, messages: List[Dict[str, Any]],
                          params: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
        cfg = self._cfg()
        web_search = params.get("web_search")
        enable_search = web_search if web_search is not None else self._bool(cfg.get("enable_search_default", False))
        model, enable_search = self._resolve_search_suffix(model, enable_search)

        backend = cfg.get("chat_backend", "agent")
        if backend not in ("agent", "copilot"):
            backend = self._prefer_backend or "agent"
        has_rich_content = any(isinstance(m.get("content"), list) for m in messages)
        # Старый COPILOT_MOA_CHAT теперь отвечает "feature has been retired".
        # Актуальный AI Chat (/api/agent/ask_proxy) принимает content parts сам.
        if has_rich_content:
            backend = "agent"
        context_mode = cfg.get("context_mode", "auto")
        reasoning_out = cfg.get("reasoning_output", "reasoning_content")
        auto_delete = self._bool(cfg.get("auto_delete_chats", True))
        # A slow reasoning/search run can legitimately take several minutes.
        # Provider-specific values still override this generous chat default.
        idle_timeout = _int_cfg(cfg, "stream_idle_timeout",
                                max(config_store.settings.stream_idle_timeout, 900), 30, 7200)
        timeout = _int_cfg(cfg, "request_timeout",
                           max(config_store.settings.request_timeout, 900), 30, 7200)

        system_text, history, final_user = normalize_messages(messages)
        # The conversation store is keyed by the client's own messages. Documents are
        # re-uploaded on every request and get a fresh private_storage_url each time, so
        # hashing the prepared copy never matched the next turn: every chat with a
        # document fell back to a full transcript in a new Genspark project.
        client_messages = messages
        try:
            if has_rich_content:
                messages = await self._prepare_files(account, messages)
            project_id, mode = await self._plan_context(account, model, client_messages, context_mode)
            prompt = self._prompt_for_mode(mode, system_text, history, final_user)

            payload, backend = self._build_payload(backend, model, prompt, messages,
                                                   project_id, mode, enable_search,
                                                   system_text, history, final_user,
                                                   has_rich_content)
            resp = await self.client.stream_chat(
                self._cookie(account), payload, backend=backend,
                proxy=self.proxy_for(account), timeout=timeout)
        except GensparkError as e:
            raise self._wrap(e) from e

        text_parts: List[str] = []
        new_project_id = ""
        got_text = False

        try:
            first_wait = _int_cfg(cfg, "first_event_timeout",
                                  max(30, config_store.settings.first_byte_timeout), 10, 3600)
            async for ev in iter_upstream_events(resp, idle_timeout=idle_timeout,
                                                 backend=backend,
                                                 first_event_timeout=first_wait):
                if ev.kind == "project":
                    if not new_project_id:
                        new_project_id = ev.project_id
                elif ev.kind == "reasoning_start":
                    continue
                elif ev.kind == "reasoning_delta" and ev.text and reasoning_out != "hidden":
                    yield {"type": "reasoning", "text": ev.text}
                elif ev.kind == "reasoning_end":
                    continue
                elif ev.kind == "text" and ev.text:
                    got_text = True
                    text_parts.append(ev.text)
                    yield {"type": "text", "text": ev.text}
                elif ev.kind == "result":
                    if ev.text and not got_text:
                        got_text = True
                        text_parts.append(ev.text)
                        yield {"type": "text", "text": ev.text}
                    break
        except (asyncio.CancelledError, GeneratorExit):
            # Остановка в MemuAI закрывает SSE и удаляет созданный проект Genspark,
            # чтобы генерация не продолжалась в аккаунте в фоне.
            # The delete runs shielded: under the ASGI cancel scope a plain await
            # here was cancelled at once and the project kept generating.
            if new_project_id:
                cookie, proxy = self._cookie(account), self.proxy_for(account)
                await http.close_quietly(
                    lambda: self.client.delete_project(cookie, new_project_id, proxy=proxy),
                    timeout=15)
            raise
        except (GensparkAuthError, GensparkRateLimitError, GensparkFreeLimitError,
                GensparkCloudflareError, GensparkContentFilterError,
                GensparkModelError) as e:
            raise self._wrap(e)
        except GensparkNetworkError as e:
            raise ProviderNetworkError(str(e))
        except GensparkServerError as e:
            # Не переключаемся на COPILOT после одного 404: этот бэкенд
            # выведен из работы и отвечает «feature has been retired».
            raise ProviderServerError(str(e), e.status or 502)
        finally:
            # Abort, not drain: after a final result or a Stop the rest of the
            # upstream stream must not be awaited (see http.close_response).
            await http.close_response(resp)

        full_text = "".join(text_parts)
        if not full_text.strip():
            raise ProviderServerError("Empty upstream response")

        await self._after_success(account, model, mode, project_id, new_project_id,
                                  client_messages, full_text, auto_delete)

    # ---- внутренние ----

    async def _prepare_files(self, account, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Преобразует вложенные документы в private_file; изображения остаются data URL."""
        prepared = json.loads(json.dumps(messages))
        for message in prepared:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for index, part in enumerate(content):
                if not isinstance(part, dict) or part.get("type") != "image_url":
                    continue
                image = part.get("image_url") or {}
                url = image.get("url") if isinstance(image, dict) else ""
                if not isinstance(url, str) or not url.startswith("data:"):
                    continue
                mime = url[5:].split(";", 1)[0].lower()
                if mime.startswith("image/"):
                    continue
                content[index] = await self.client.upload_private_file(
                    self._cookie(account), url, str(part.get("filename") or "file"),
                    proxy=self.proxy_for(account))
        return prepared

    @staticmethod
    def _cookie(account) -> str:
        cookie = str(account.fields.get("cookie", "")).strip()
        if cookie and not cookie.lower().startswith("session_id=") and "session_id=" not in cookie:
            cookie = f"session_id={cookie}"
        return cookie

    @staticmethod
    def _resolve_search_suffix(model: str, enable_search: bool) -> Tuple[str, bool]:
        if model.endswith("-search"):
            return model[: -len("-search")], True
        return model, enable_search

    @staticmethod
    def _wrap(e: GensparkError) -> ProviderError:
        if isinstance(e, GensparkContentFilterError):
            return ProviderContentFilterError(str(e))
        if isinstance(e, GensparkModelError):
            return ProviderModelUnavailableError(str(e))
        if isinstance(e, GensparkAuthError):
            return ProviderAuthError(str(e))
        if isinstance(e, GensparkRateLimitError):
            return ProviderRateLimitError(str(e))
        if isinstance(e, GensparkFreeLimitError):
            return ProviderQuotaError(str(e))
        if isinstance(e, GensparkCloudflareError):
            return ProviderRateLimitError(str(e))
        if isinstance(e, GensparkNetworkError):
            return ProviderNetworkError(str(e))
        if isinstance(e, GensparkServerError):
            return ProviderServerError(str(e), e.status or 502)
        return ProviderServerError(str(e))

    async def _plan_context(self, account, model: str, messages: List[Dict[str, Any]],
                            mode_setting: str) -> Tuple[str, str]:
        if mode_setting == "stateless":
            return "", "transcript" if _history_needed(messages) else "fresh"
        if mode_setting == "server":
            conv = await conversations.find_latest(model, account.id)
            if conv:
                conv.last_used = time.time()
                return conv.project_id, "continue"
            return "", "fresh"
        conv = await conversations.match(model, messages[:-1], account.id)
        if conv:
            conv.last_used = time.time()
            return conv.project_id, "continue"
        return "", ("fresh" if not _history_needed(messages) else "transcript")

    @staticmethod
    def _prompt_for_mode(mode: str, system_text: str, history: List[Dict[str, str]],
                         final_user: str) -> str:
        if mode == "continue":
            return final_user
        return transcript_prompt(system_text, history, final_user)

    def _build_payload(self, backend: str, model: str, prompt: str, messages: List[Dict[str, Any]],
                       project_id: str, mode: str, enable_search: bool,
                       system_text: str, history: List[Dict[str, str]],
                       final_user: str, has_rich_content: bool = False) -> Tuple[Dict[str, Any], str]:
        if backend == "copilot":
            if has_rich_content:
                rich = [
                    {"role": m.get("role", "user"), "content": m.get("content", "")}
                    for m in messages if m.get("role") not in ("system", "developer")
                    and not (m.get("role") == "assistant" and not m.get("content"))
                ]
                if mode == "continue":
                    last_user = next((m for m in reversed(rich) if m["role"] == "user"), None)
                    copilot_messages = [last_user] if last_user else rich[-1:]
                else:
                    copilot_messages = rich
                    if system_text:
                        copilot_messages.insert(0, {"role": "system", "content": system_text})
            elif mode == "continue" or not history:
                copilot_messages = [{"role": "user", "content": final_user}]
            else:
                copilot_messages = ([{"role": "system", "content": system_text}] if system_text else [])
                copilot_messages += [{"role": m["role"], "content": m["content"]} for m in history]
                copilot_messages.append({"role": "user", "content": final_user})
            payload = self.client.build_copilot_payload(
                model=model, messages=copilot_messages, request_web_knowledge=enable_search)
            if project_id:
                payload["current_query_string"] = f"id={project_id}&type=COPILOT_MOA_CHAT"
            return payload, "copilot"
        if has_rich_content:
            rich = [{"role": m.get("role", "user"), "content": m.get("content", "")}
                    for m in messages if m.get("role") not in ("system", "developer")
                    and not (m.get("role") == "assistant" and not m.get("content"))]
            if mode == "continue":
                last_user = next((m for m in reversed(rich) if m["role"] == "user"), None)
                send_messages = [last_user] if last_user else rich[-1:]
            else:
                send_messages = rich
                # AI Chat не имеет отдельной system-роли: добавляем инструкцию
                # к текстовой части первого актуального запроса, не трогая image_url.
                if system_text and send_messages:
                    target = next((m for m in send_messages if m["role"] == "user"), send_messages[0])
                    content = target.get("content")
                    if isinstance(content, list):
                        content.insert(0, {"type": "text", "text": system_text + "\n\n"})
                    else:
                        target["content"] = system_text + "\n\n" + str(content or "")
            for msg in send_messages:
                msg.setdefault("id", str(uuid.uuid4()))
        else:
            send_messages = [{"role": "user", "id": str(uuid.uuid4()), "content": prompt}]
        payload = self.client.build_agent_payload(
            model=model, user_s_input=prompt, messages=send_messages,
            project_id=project_id or None, enable_search=enable_search)
        return payload, "agent"

    async def _after_success(self, account, model: str, mode: str, old_project_id: str,
                             new_project_id: str, messages: List[Dict[str, Any]],
                             full_text: str, auto_delete: bool) -> None:
        project_id = new_project_id or old_project_id
        if not project_id:
            return
        if mode == "transcript":
            if auto_delete:
                spawn_background(self.client.delete_project(
                    self._cookie(account), project_id, proxy=self.proxy_for(account)))
            return
        tail = list(messages) + [{"role": "assistant", "content": full_text}]
        await conversations.add(Conv(project_id=project_id, account_id=account.id, model=model,
                                     tail_len=len(tail),
                                     tail_hash=ConversationStore.hash_messages(tail),
                                     last_used=time.time()))

    # ---- изображения ----

    async def generate_images(self, account, prompt: str, model: str = "auto",
                              aspect: str = "auto",
                              reference_images: Optional[List[str]] = None,
                              *, media: str = "image") -> List[str]:
        """COPILOT_MOA_IMAGE or COPILOT_MOA_VIDEO → task_id → ig_tasks_status → URL."""
        cookie = self._cookie(account)
        model = (model or "auto").strip()
        video = media == "video"
        kind = "COPILOT_MOA_VIDEO" if video else "COPILOT_MOA_IMAGE"
        content_parts = [
            {"type": "image_url", "image_url": {"url": image}}
            for image in (reference_images or [])[:10]
        ]
        content_parts.append({"type": "text", "text": prompt})
        selected_models = GS_IMAGE_AUTO_MODELS if (not video and model.lower() == "auto") else [model]
        payload = {
            "type": kind,
            "current_query_string": f"type={kind}",
            "messages": [{"role": "user", "content": content_parts
                          if reference_images else prompt}],
            "user_s_input": prompt,
            "action_params": {},
            "extra_data": {
                "model_configs": [{
                    "model": selected, "aspect_ratio": aspect,
                    "use_personalized_models": False, "fashion_profile_id": None,
                    "hd": False, "reflection_enabled": False, "style": "auto",
                    "image_size": "auto",
                } for selected in selected_models],
                "llm_model": "gpt-4o",
                "imageModelMap": {},
                "writingContent": None,
            },
        }
        try:
            resp = await self.client.image_ask(cookie, payload, proxy=self.proxy_for(account),
                                               timeout=300)
        except GensparkError as e:
            raise self._wrap(e) from e
        body = resp.text
        if resp.status_code != 200:
            if looks_like_cloudflare(body):
                raise ProviderRateLimitError(
                    f"Cloudflare blocked the generation (HTTP {resp.status_code})")
            if resp.status_code in (401, 403):
                raise ProviderAuthError(f"The cookie was rejected during generation (HTTP {resp.status_code})")
            if resp.status_code == 429:
                raise ProviderRateLimitError("Image generation rate limit (HTTP 429)")
            if resp.status_code == 402:
                raise ProviderQuotaError("The image generation limit is used up (HTTP 402)")
            raise ProviderServerError(f"Генерация: HTTP {resp.status_code}", resp.status_code)

        task_ids = []
        direct_urls: List[str] = []
        def collect(value: Any, depth: int = 0) -> None:
            if depth > 7:
                return
            if isinstance(value, str):
                stripped = value.strip()
                if stripped.startswith(("{", "[")):
                    try: collect(json.loads(stripped), depth + 1)
                    except Exception: pass
                elif stripped.startswith("http") and any(x in stripped.lower() for x in (
                        (".mp4", ".webm", ".mov") if video else (".png", ".jpg", ".jpeg", ".webp"))):
                    direct_urls.append(stripped)
            elif isinstance(value, dict):
                tid = value.get("task_id") or value.get("taskId")
                if tid: task_ids.append(str(tid))
                for key, child in value.items():
                    if key in ("image_url", "url", "video_url") and isinstance(child, str) and child.startswith("http"):
                        direct_urls.append(child)
                    collect(child, depth + 1)
            elif isinstance(value, list):
                for child in value: collect(child, depth + 1)
        for line in body.splitlines():
            line = line.strip()
            try: collect(json.loads(line.removeprefix("data: ").strip()))
            except Exception: pass
        task_ids = list(dict.fromkeys(task_ids))
        direct_urls = list(dict.fromkeys(direct_urls))
        if not task_ids:
            if direct_urls:
                return direct_urls
            if "rate limit" in body.lower():
                raise ProviderRateLimitError("Image generation rate limit")
            if "free usage limit" in body.lower():
                raise ProviderQuotaError("The daily generation limit is used up")
            # Для устаревшего/временно выключенного id пробуем официальный
            # набор Auto. Он раскрывается в конкретные модели выше, а не
            # отправляется как несуществующий upstream-id ``auto``.
            if (not video and model.lower() != "auto" and not reference_images
                    and "moa_image_result" in body):
                return await self.generate_images(account, prompt, "auto", aspect, reference_images)
            # Не выводим пользователю сырой хвост SSE: там были UUID и
            # внутренние message_field, но не было полезной причины ошибки.
            raise ProviderServerError(
                "Genspark AI Image did not create a task. Refresh the account cookie; "
                "если ошибка повторится, откройте AI Image на genspark.ai один раз "
                "и повторите запрос"
            )

        try:
            poll = await self.client.ig_tasks_status(cookie, task_ids, proxy=self.proxy_for(account))
        except GensparkError as e:
            raise self._wrap(e) from e
        urls: List[str] = []
        try:
            async for line in http.aiter_lines_str(poll):
                line = (line or "").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    obj = json.loads(line[5:].strip())
                except Exception:
                    continue
                if obj.get("type") == "TASKS_STATUS_COMPLETE":
                    final = obj.get("final_status") or {}
                    for tid in task_ids:
                        task = final.get(tid) or {}
                        if task.get("status") == "SUCCESS":
                            for u in (task.get("video_urls") or task.get("image_urls") or []):
                                urls.append(u)
        finally:
            await http.close_response(poll)
        if not urls:
            raise ProviderServerError("Generation did not reach the success state in time")
        # AI Image projects are intentionally kept: users expect a completed
        # generation to remain visible in Genspark's own history.
        return urls

    # ---- каталог моделей ----

    async def refresh_models_config(self) -> Dict[str, Any]:
        """Image/audio/video модели из /api/models_config (для справки)."""
        from ..state import state as app_state
        accounts = app_state.usable_accounts("genspark")
        # The same normalisation as chat: a bare session id needs its «session_id=» name.
        cookie = self._cookie(accounts[0]) if accounts else ""
        data = await self.client.models_config(cookie=cookie, proxy=self.proxy_for(accounts[0]) if accounts else "")
        sections = gs_catalog.parse_models_config(data)
        return sections

    async def discover_models(self) -> Dict[str, Any]:
        from ..state import state as app_state
        accounts = app_state.usable_accounts("genspark")
        if not accounts:
            raise GensparkError("Нет доступных аккаунтов Genspark")
        account = accounts[0]
        res = await gs_catalog.discover_chat_models(
            self.client, self._cookie(account), proxy=self.proxy_for(account))
        res["ids"] = [model for model in (res.get("ids") or []) if is_chat_model(model)]
        return res
