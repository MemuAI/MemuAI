"""Managed Free gateway, native Zen/Go and existing opencode2api integration.

Free uses a separately installed upstream executable. Paid connections use
the native APIs; this Python adapter does not link third-party gateway code.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

from . import http
from .base import (Field, ProviderAuthError, ProviderError,
                   ProviderModelUnavailableError, ProviderNetworkError,
                   ProviderServerError, ProviderRateLimitError,
                   retry_after_seconds, safe_provider_error)
from .custom import CustomProvider
from . import opencode_catalog as catalog


class OpenCodeProvider(CustomProvider):
    id = "opencode"
    title = "OpenCode"
    icon = "▤"
    description = "Бесплатные модели через устанавливаемый opencode2api, ключ Zen/Go или свой шлюз. У каждого способа — отдельная пошаговая настройка."
    description_en = "Free models through an installed opencode2api gateway, a Zen/Go key, or an existing gateway. Each mode has its own setup steps."
    authoritative_empty_catalog = True

    def __init__(self):
        self._locks = {}
        self._free_probes = {}

    def account_fields(self):
        return [
            Field("mode", "Способ подключения", "select", options=["free", "zen", "go", "gateway"], default="free",
                  help="Free — MemuAI скачает отдельный шлюз по кнопке ниже; Zen/Go — свой ключ; gateway — уже запущенный сервер.",
                  label_en="Connection", help_en="Free installs a separate gateway using the button below; Zen/Go use your own key; gateway connects an existing server."),
            Field("api_key", "API-ключ", "password", required=False,
                  help="Free — без ключа. Zen/Go: opencode.ai/auth → вход → рабочее пространство → API-ключ. Свой шлюз: server_keys (jason) или API_KEY (Tiara).",
                  label_en="API key", help_en="Free needs no key. Zen/Go: opencode.ai/auth → sign in → workspace → API key. Existing gateway: server_keys (jason) or API_KEY (Tiara)."),
            Field("base_url", "Адрес шлюза opencode2api", "url", required=False, placeholder="http://127.0.0.1:8080/v1",
                  help="jason: http://127.0.0.1:8080/v1; Tiara: http://127.0.0.1:10000/v1. Порт 8081 — панель, не API. Сервер запускается отдельно.",
                  label_en="opencode2api gateway URL", help_en="jason: http://127.0.0.1:8080/v1; Tiara: http://127.0.0.1:10000/v1. Port 8081 is the dashboard, not the API. Start your server separately."),
            Field("api_protocol", "API шлюза", "select", required=False, default="chat", options=["chat", "responses"],
                  help="Только для gateway. У прямого подключения протокол выбирается автоматически для каждой модели.",
                  label_en="Gateway API", help_en="Gateway mode only. Native connections select the protocol from each model's metadata."),
        ]

    def seed_models(self):
        return []

    def validate_fields(self, fields):
        if any(not isinstance(value, (str, int, float, bool)) and value is not None for value in fields.values()):
            return "Поля OpenCode должны быть строками / OpenCode fields must be strings"
        mode = catalog.mode_of(fields)
        if mode not in ("free", "zen", "go", "gateway"):
            return "Выберите Free, Zen, Go или gateway / Choose Free, Zen, Go or gateway"
        key = str(fields.get("api_key", "") or "").strip()
        if len(key) > 4096 or any(ord(c) < 33 or ord(c) == 127 for c in key):
            return "API-ключ должен быть одной строкой без пробелов / Enter one API key without spaces"
        if mode in ("zen", "go") and not key:
            return "Для Zen и Go нужен API-ключ из opencode.ai/auth / Zen and Go need an API key from opencode.ai/auth"
        if mode == "gateway":
            base = str(fields.get("base_url", "") or "").strip()
            try:
                url = urlsplit(base)
                invalid = (not url.hostname or url.username or url.password or url.query or url.fragment
                           or url.scheme not in ("http", "https") or len(base) > 4096 or "\\" in base
                           or any(ord(c) < 33 or ord(c) == 127 for c in base)
                           or any(part in (".", "..") for part in url.path.split("/")))
                if url.scheme == "http" and not http.is_loopback(base):
                    invalid = True
            except ValueError:
                invalid = True
            if invalid:
                return "Укажите HTTPS URL шлюза или HTTP на localhost / Use an HTTPS gateway or HTTP localhost"
        if str(fields.get("api_protocol", "chat") or "chat") not in ("chat", "responses"):
            return "Неизвестный API шлюза / Unknown gateway API"
        return None

    @staticmethod
    def base_of(account):
        return catalog.gateway_base(account.fields.get("base_url", "")) if catalog.mode_of(account.fields) == "gateway" else "https://opencode.ai"

    @staticmethod
    async def _free_connection(account):
        from ..opencode_runtime import runtime
        try:
            connection = await runtime.ensure(account.id, CustomProvider().proxy_for(account),
                                              models=tuple(OpenCodeProvider._snapshot(account).get("models", {})))
        except (RuntimeError, OSError) as exc:
            raise ProviderServerError(safe_provider_error(exc, account), 503) from exc
        # Ephemeral credentials never enter account.extra or its public model metadata.
        return SimpleNamespace(id=account.id, proxy="", extra={}, fields={
            "mode": "gateway", "base_url": connection["base_url"], "api_key": connection["key"]})

    async def close(self):
        from ..opencode_runtime import runtime
        await asyncio.to_thread(runtime.shutdown)

    def proxy_for(self, account):
        return CustomProvider.proxy_for(self, account)

    def _headers(self, account, accept="application/json", api_key=None):
        mode = catalog.mode_of(account.fields)
        key = "" if mode == "free" else str(account.fields.get("api_key", "") or "").strip()
        # Native APIs explicitly welcome clients identifying themselves, rather
        # than impersonating OpenCode's own CLI or an SDK.
        result = {"Accept": accept, "Content-Type": "application/json", "User-Agent": "MemuAI/3.8"}
        if key:
            result["Authorization"] = "Bearer " + key
        return result

    async def _get_json(self, account, url, *, credential=True, limit=8_388_608):
        try:
            session = await http.get_session(self.proxy_for(account), impersonate=False, url=url)
            headers = self._headers(account) if credential else {"Accept": "application/json", "User-Agent": "MemuAI/3.8"}
            response = await session.get(url, headers=headers, timeout=30, allow_redirects=False, stream=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ProviderNetworkError("Network error: " + http.network_error_text(exc)) from exc
        try:
            status = response.status_code
            if 300 <= status < 400:
                raise ProviderServerError("OpenCode returned a redirect; credentials were not forwarded", 502)
            chunks, size = [], 0
            async for chunk in response.aiter_content():
                chunk = chunk.encode("utf-8") if isinstance(chunk, str) else bytes(chunk)
                size += len(chunk)
                if status != 200:
                    chunks.append(chunk[:max(0, 3000 - sum(map(len, chunks)))])
                    if size >= 3000:
                        break
                else:
                    if size > limit:
                        raise ProviderServerError("OpenCode model/usage response is too large")
                    chunks.append(chunk)
            raw = b"".join(chunks)
            if status != 200:
                if catalog.mode_of(account.fields) == "gateway" and status == 404:
                    raise ProviderServerError("На этом адресе не найден API /models. jason: http://127.0.0.1:8080/v1; Tiara: http://127.0.0.1:10000/v1. Порт 8081 обычно является панелью, а не API / Gateway model API not found; check the API port, not the WebUI port", 404)
                text = safe_provider_error(raw.decode("utf-8", "replace"), account)
                await self._raise_for_status(status, text, retry_after=retry_after_seconds(getattr(response, "headers", None)))
            try:
                value = json.loads(raw)
            except Exception as exc:
                message = "Шлюз вернул не JSON: вероятно, указан адрес WebUI вместо API / Gateway did not return JSON; check that this is the API, not its WebUI" if catalog.mode_of(account.fields) == "gateway" else "OpenCode returned invalid JSON"
                raise ProviderServerError(message) from exc
            if not isinstance(value, dict):
                raise ProviderServerError("OpenCode returned an invalid response")
            return value
        except (ProviderError, asyncio.CancelledError):
            raise
        except Exception as exc:
            raise ProviderNetworkError("Network error: " + http.network_error_text(exc)) from exc
        finally:
            await http.close_response(response)

    @staticmethod
    def _snapshot(account):
        value = account.extra.get("opencode_catalog") or {}
        return value if isinstance(value, dict) and value.get("fingerprint") == catalog.fingerprint(account.fields) else {}

    async def dynamic_models(self, account):
        mode = catalog.mode_of(account.fields)
        error = self.validate_fields(account.fields)
        if error:
            raise ProviderServerError(error, 400)
        stamp = catalog.fingerprint(account.fields)
        loop = asyncio.get_running_loop()
        lock = self._locks.setdefault((id(loop), account.id), asyncio.Lock())
        async with lock:
            # The upstream gateway runs automatic generation probes on startup.
            # A background catalog refresh must never start that executable.
            connection = account
            endpoint = self.base_of(connection) + "/models" if mode == "gateway" else catalog.MODELS_URLS[mode]
            try:
                if mode == "go":
                    # Listing alone can be public: this read-only endpoint proves
                    # that the key really has a Go subscription before exposing it.
                    await self.usage(account, refresh=True)
                data = await self._get_json(connection, endpoint)
                listed = catalog.model_ids(data)
                if mode == "gateway":
                    models = {row["id"]: {**row, "protocol": str(account.fields.get("api_protocol") or "chat"),
                                            "api": self.base_of(account)} for row in listed}
                else:
                    metadata = await self._get_json(account, catalog.METADATA_URL, credential=False, limit=67_108_864)
                    declared = catalog.metadata_models(metadata, mode)
                    models = {row["id"]: {**declared[row["id"]], **{k: v for k, v in row.items()
                               if k not in ("protocol", "api", "provider")}}
                              for row in listed if row["id"] in declared}
                if catalog.fingerprint(account.fields) != stamp:
                    raise ProviderServerError("OpenCode connection changed during model discovery; refresh again", 409)
                account.extra["opencode_catalog"] = {"fingerprint": stamp, "checked": time.time(),
                                                       "models": models, "mode": mode, "source": endpoint}
                self._save_account(account)
                if models:
                    try:
                        from .. import model_capabilities, model_info
                        for name, row in models.items():
                            limits = row.get("limit") if isinstance(row.get("limit"), dict) else {}
                            model_info.record("opencode", name, context=limits.get("context"),
                                              output=limits.get("output"), source="api:opencode")
                        rows = []
                        for row in models.values():
                            limits = row.get("limit") if isinstance(row.get("limit"), dict) else {}
                            cost = row.get("cost") if isinstance(row.get("cost"), dict) else {}
                            rows.append({**row, "context_window": limits.get("context") or row.get("context_window"),
                                         "max_output_tokens": limits.get("output") or row.get("max_output_tokens"),
                                         "pricing": {key: cost[key] for key in ("input", "output") if key in cost}
                                         or row.get("pricing", {})})
                        model_capabilities.record_raw("opencode", rows, source="api:opencode")
                    except Exception:
                        pass
                return list(models)
            except (ProviderAuthError, ProviderModelUnavailableError):
                account.extra["opencode_catalog"] = {"fingerprint": stamp, "checked": time.time(), "models": {},
                                                       "mode": mode, "source": endpoint, "denied": True}
                self._save_account(account)
                raise
            except ValueError as exc:
                raise ProviderServerError(str(exc)) from exc

    @staticmethod
    def _save_account(account):
        from ..state import state
        if state.get_account(account.id) is account:
            state.save_accounts()

    def account_model_available(self, account, model):
        snapshot = self._snapshot(account)
        # Never route a guessed ID or reuse an old key's catalogue.
        return bool(snapshot and model in snapshot.get("models", {}))

    def model_availability(self, model, accounts):
        snapshots = [self._snapshot(account) for account in accounts]
        allowed = any(model in row.get("models", {}) for row in snapshots)
        checked = max((row.get("checked", 0) for row in snapshots), default=0)
        return {"available": allowed, "reason": "" if allowed else "Модель отсутствует в доступном каталоге подключённых аккаунтов OpenCode",
                "source": "api:opencode", "checked": checked}

    async def check(self, account):
        try:
            names = await self.dynamic_models(account)
            if not names:
                return None, "OpenCode отвечает, но доступных поддерживаемых моделей нет; обновите каталог позже"
            mode = catalog.mode_of(account.fields)
            if mode == "free":
                return True, (f"Каталог Free доступен · моделей: {len(names)}. Генерация не проверена. "
                              "Это анонимный доступ, не ваш аккаунт OpenCode; откройте настройки подключения → Проверить генерацию.")
            return True, f"подключено · {mode} · доступных моделей: {len(names)}"
        except ProviderAuthError as exc:
            return False, safe_provider_error(exc, account)
        except ProviderModelUnavailableError as exc:
            return False, safe_provider_error(exc, account)
        except ProviderError as exc:
            return None, safe_provider_error(exc, account)

    async def probe_free(self, account):
        """Explicit tiny free-only probe; never run by catalog refresh/check()."""
        if catalog.mode_of(account.fields) != "free":
            raise ValueError("Проверка предназначена только для анонимного Free / Anonymous Free only")
        stamp = catalog.fingerprint(account.fields) + hashlib.sha256(self.proxy_for(account).encode()).hexdigest()
        key = (account.id, stamp)
        now = time.monotonic()
        previous = self._free_probes.get(key)
        if previous and now - previous["at"] < 60:
            return {**(previous.get("result") or {"ok": False, "status": "running", "checked": time.time()}),
                    "cached": True, "retry_after": max(1, int(60 - (now - previous["at"])))}
        self._free_probes = {k: v for k, v in self._free_probes.items() if now - v["at"] < 60}
        entry = self._free_probes[key] = {"at": now}
        result = {"ok": False, "status": "error", "checked": time.time(), "scope": "anonymous_public"}
        try:
            names = await self.dynamic_models(account)
            # Recheck actual zero prices; never pick a paid row or fall back to a key.
            models = self._snapshot(account).get("models", {})
            model = next((name for name in names if all(
                isinstance((models[name].get("cost") or {}).get(k), (int, float))
                and not isinstance(models[name]["cost"][k], bool) and models[name]["cost"][k] == 0
                for k in ("input", "output"))), None)
            if model is None:
                raise ProviderServerError("Нет подтверждённой бесплатной модели / No verified zero-price model", 503)
            result["model"] = model
            source = self.chat_stream(account, model, [{"role": "user", "content": "Reply OK."}], {"max_tokens": 16})
            generated = False
            try:
                async for event in source:
                    generated = generated or bool(event.get("text"))
            finally:
                await source.aclose()
            result.update(ok=generated, status="ok" if generated else "empty", http_status=200,
                          message="Free ответил на тестовый запрос." if generated else "Запрос завершён без текста; генерация не подтверждена.",
                          message_en="Free answered the test request." if generated else "Request finished without text; generation is not confirmed.")
        except asyncio.CancelledError:
            raise
        except ProviderError as exc:
            from .base import provider_error_texts
            ru, en = provider_error_texts(exc, account)
            result.update(http_status=exc.status, message=ru, message_en=en,
                          status=getattr(exc, "opencode_reason", "error"))
        finally:
            entry["result"] = result
        return result

    async def usage(self, account, *, refresh=False):
        mode = catalog.mode_of(account.fields)
        note = "OpenCode Go возвращает общие окна подписки, а не лимиты каждой модели. Ключи одной подписки нельзя складывать."
        result = {"plan": mode, "scope": "anonymous_public" if mode == "free" else "shared_subscription", "source": catalog.USAGE_URL,
                  "checked": time.time(), "available_models": list(self._snapshot(account).get("models", {})),
                  "note": note, "note_en": "Go windows are shared across the subscription, not per-model limits. Keys may share one subscription; do not sum them."}
        if mode != "go":
            return {**result, "status": "unavailable", "windows": [], "source": "",
                    "note": "Числовые лимиты доступны для прямого Go. Free использует общий публичный доступ без входа в аккаунт. Ответ 429 не доказывает, что вы потратили личную квоту; причина и срок восстановления сервисом не сообщаются. Вторая запись Free не создаёт отдельную квоту.",
                    "note_en": "Numeric limits are available for native Go. Free uses anonymous public access. A 429 does not prove your personal quota is exhausted; the service does not explain the exact cause or reset time. Another Free entry does not create a separate quota.",
                    "rows": [], "notice": "Числовые лимиты этого подключения OpenCode API не публикует.",
                    "notice_en": "This OpenCode connection does not publish numeric limits."}
        stamp = catalog.fingerprint(account.fields)
        previous = account.extra.get("opencode_usage") or {}
        if not refresh and previous.get("fingerprint") == stamp and time.time() - previous.get("checked", 0) < 60:
            return {**previous["data"], "available_models": result["available_models"]}
        try:
            data = await self._get_json(account, catalog.USAGE_URL)
            result.update(status="ok", windows=catalog.quota_windows(data))
            result.update(subscription="OpenCode Go", notice=result["note"], notice_en=result["note_en"],
                          rows=[{"display": row["label"], "display_en": row["label_en"],
                                 "used": row["used_percent"], "limit": 100, "unit": "%", "unit_en": "%",
                                 "reset": row["reset_at"]} for row in result["windows"]])
            if stamp != catalog.fingerprint(account.fields):
                raise ProviderServerError("OpenCode connection changed during quota discovery", 409)
            account.extra["opencode_usage"] = {"fingerprint": stamp, "checked": result["checked"], "data": result}
            self._save_account(account)
            return result
        except ValueError as exc:
            raise ProviderServerError(str(exc)) from exc

    async def chat_stream(self, account, model, messages, params):
        snapshot = self._snapshot(account)
        if not snapshot or time.time() - snapshot.get("checked", 0) > 900:
            await self.dynamic_models(account)
            snapshot = self._snapshot(account)
        row = snapshot.get("models", {}).get(model)
        if not row:
            raise ProviderModelUnavailableError("This OpenCode account has no access to this model", model,
                                               message_ru="У этого аккаунта OpenCode нет доступа к выбранной модели")
        # The first conversation message is stable through ordinary tool turns;
        # explicit session IDs from clients take precedence when provided.
        explicit = params.get("session_id") or params.get("conversation_id") or params.get("_session_id")
        seed = str(explicit) if explicit else json.dumps(next((m for m in messages if m.get("role") == "user"), {}), sort_keys=True, ensure_ascii=False)
        session_id = hashlib.sha256((account.id + "\0" + seed).encode()).hexdigest()[:32]
        headers = {**self._headers(account), "x-opencode-session": session_id}
        if catalog.mode_of(account.fields) == "free":
            connection = await self._free_connection(account)
            view = SimpleNamespace(id=account.id, proxy="", extra=account.extra, fields={
                "base_url": connection.fields["base_url"], "api_key": connection.fields["api_key"],
                "api_protocol": "chat", "headers": json.dumps({"x-opencode-session": session_id})})
            source = CustomProvider().chat_stream(view, model, messages, params)
            try:
                async for event in source:
                    yield event
            except ProviderRateLimitError as exc:
                from ..opencode_runtime import runtime
                await asyncio.to_thread(runtime.stop, account.id)
                detail = safe_provider_error(exc, view)
                error = ProviderRateLimitError(
                    "OpenCode anonymous Free was refused (HTTP 429). This is public access, not your signed-in account; "
                    "it does not prove personal quota exhaustion. The gateway can mask client/network restrictions with its final 429. "
                    "Another Free entry will use the same public access. No reset time is known. Upstream: " + detail,
                    retry_after=exc.retry_after if exc.retry_after is not None else 60)
                error.message_ru = ("OpenCode отказал анонимному Free (HTTP 429). Это общий публичный доступ, а не ваш аккаунт: "
                    "ошибка не доказывает расход вашей личной квоты. Итоговый 429 шлюза может скрывать ограничение клиента или сети. "
                    "Вторая запись Free использует тот же публичный доступ; срок восстановления неизвестен. Ответ сервиса: " + detail)
                error.opencode_reason = "anonymous_rejected"
                raise error from exc
            except ProviderAuthError as exc:
                from ..opencode_runtime import runtime
                await asyncio.to_thread(runtime.stop, account.id)
                detail = safe_provider_error(exc, view)
                error = ProviderRateLimitError("OpenCode rejected anonymous public access, not your personal API key. "
                    "Free may require the official OpenCode client or reject this network. Upstream: " + detail,
                    retry_after=60)
                # Preserve the upstream status without treating an anonymous
                # refusal as a broken user key or a globally invalid request.
                error.status = 403
                error.message_ru = ("OpenCode отклонил публичный Free, а не ваш личный API-ключ. "
                    "Бесплатный доступ может требовать официальный клиент OpenCode или быть недоступным из этой сети. Ответ сервиса: " + detail)
                error.opencode_reason = "anonymous_forbidden"
                raise error from exc
            finally:
                await source.aclose()
            return
        protocol = row["protocol"]
        if protocol in ("messages", "gemini"):
            from .opencode_native import stream
            native_params = dict(params)
            ledger = None
            if protocol == "gemini":
                # generateContent requires opaque thought signatures to be
                # returned with tool calls. Keep them private at this boundary;
                # general clients only need the standard call ID.
                ledger = account.extra.get("opencode_thought_tokens")
                if not isinstance(ledger, dict) or ledger.get("fingerprint") != catalog.fingerprint(account.fields):
                    ledger = {"fingerprint": catalog.fingerprint(account.fields), "calls": {}}
                    account.extra["opencode_thought_tokens"] = ledger
                calls = ledger.get("calls") if isinstance(ledger.get("calls"), dict) else {}
                ledger["calls"] = {key: value for key, value in list(calls.items())[-256:]
                                   if isinstance(value, dict) and isinstance(value.get("at"), (int, float))
                                   and 0 <= time.time() - value["at"] < 7 * 86400
                                   and isinstance(value.get("signature", ""), str) and len(value.get("signature", "")) <= 262144
                                   and isinstance(value.get("args"), dict)}
                native_params["_opencode_signature_cache"] = ledger["calls"]
            source = stream(self, account, model, row, messages, native_params, headers)
            try:
                async for event in source:
                    yield event
            finally:
                await source.aclose()
                if ledger is not None:
                    from ..state import state
                    if state.get_account(account.id) is account:
                        state.save_accounts_soon()
            return
        # Reuse the tested transport: cancellation, usage, reasoning, images,
        # native tools and Responses translation are identical to Custom API.
        view = SimpleNamespace(**{key: getattr(account, key) for key in ("id", "proxy", "extra")},
                               fields={"base_url": row["api"], "api_protocol": protocol,
                                       "api_key": "" if catalog.mode_of(account.fields) == "free" else str(account.fields.get("api_key", "") or ""),
                                       "headers": json.dumps(headers)})
        # Custom's methods need their normal field-based headers/base, rather
        # than this provider's native mode-based overrides.
        adapter = CustomProvider()
        source = adapter.chat_stream(view, model, messages, params)
        try:
            async for event in source:
                yield event
        finally:
            await source.aclose()
