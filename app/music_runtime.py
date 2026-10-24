"""Music generation through Proxy accounts, with declarative REST adapters."""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from urllib.parse import urljoin, urlsplit

from . import module_media
from .routing import resolve
from .state import state

TEMPLATE = {key: "{{" + key + "}}" for key in
            ("model", "prompt", "lyrics", "style", "duration", "tempo", "instrumental")}
PRESETS = [
    {"id": "elevenlabs", "title": "ElevenLabs Music", "base_url": "https://api.elevenlabs.io/v1",
     "models": ["music_v2_5", "music_v2", "music_v1"], "docs": "https://elevenlabs.io/docs/api-reference/music/compose",
     "config": {"preset": "elevenlabs", "endpoint": "music", "auth": "header", "auth_header": "xi-api-key"}},
    {"id": "minimax", "title": "MiniMax Music · existing accounts", "base_url": "https://api.minimax.io/v1",
     "models": ["music-3.0", "music-2.6"], "docs": "https://platform.minimax.io/docs/api-reference/music-generation",
     "config": {"preset": "minimax", "endpoint": "music_generation", "auth": "bearer"}},
    {"id": "generic", "title": "Custom JSON / audio API", "base_url": "",
     "models": [], "docs": "", "config": {"endpoint": "audio/music", "auth": "bearer", "template": TEMPLATE,
        "audio_path": "data.0.url", "base64_path": "data.0.b64_json", "job_id_path": "id", "poll_url": "",
        "status_path": "status", "ready_statuses": ["completed", "succeeded", "done"],
        "failed_statuses": ["failed", "error", "cancelled"]}},
]
_JOBS = {}


def model_names(fields):
    return list(dict.fromkeys(value.strip() for value in re.split(r"[,\n;]", str(fields.get("music_models", "") or ""))
                              if value.strip()))[:100]


def parse_config(fields):
    raw = fields.get("music_config", "") or ""
    if not raw:
        return {}
    try:
        config = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("Настройки музыки должны быть JSON-объектом / Music settings must be a JSON object") from exc
    if not isinstance(config, dict) or len(json.dumps(config)) > 24000:
        raise ValueError("Некорректные настройки музыки / Invalid music settings")
    allowed = {"preset", "endpoint", "auth", "auth_header", "auth_prefix", "template", "audio_path", "base64_path",
               "hex_path", "job_id_path", "poll_url", "status_path", "ready_statuses", "failed_statuses", "error_path"}
    if set(config) - allowed:
        raise ValueError("Неизвестное поле настроек музыки; ключ хранится в аккаунте / Unknown music setting; keep the key in the account")
    if config.get("auth", "bearer") not in ("bearer", "header", "google", "anthropic", "none"):
        raise ValueError("Некорректная авторизация музыки / Invalid music authentication")
    if config.get("preset", "generic") not in ("generic", "elevenlabs", "minimax"):
        raise ValueError("Неизвестный музыкальный адаптер / Unknown music adapter")
    if not isinstance(config.get("template", {}), dict):
        raise ValueError("Шаблон музыки должен быть JSON-объектом / Music template must be a JSON object")
    header = str(config.get("auth_header", "x-api-key"))
    if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", header) or any(char in str(config.get("auth_prefix", "")) for char in "\r\n"):
        raise ValueError("Некорректный заголовок авторизации / Invalid authentication header")
    for key in ("ready_statuses", "failed_statuses"):
        if key in config and (not isinstance(config[key], list) or len(config[key]) > 20
                              or any(not isinstance(value, (str, int)) for value in config[key])):
            raise ValueError("Некорректные статусы задания / Invalid job statuses")
    base = str(fields.get("base_url", "")).rstrip("/") + "/"
    endpoint = urljoin(base, str(config.get("endpoint", "audio/music")))
    origin = urlsplit(base)
    target = urlsplit(endpoint)
    if origin.scheme != "https" or target.scheme != "https" or target.username or target.password or target[:2] != origin[:2]:
        raise ValueError("Музыкальный endpoint должен быть HTTPS на том же API / Music endpoint must use HTTPS on the account API origin")
    poll = str(config.get("poll_url", ""))
    if poll and ("{id}" not in poll or urlsplit(urljoin(endpoint, poll))[:2] != target[:2]):
        raise ValueError("Опрос музыки должен содержать {id} и использовать тот же API / Music polling requires {id} on the same API origin")
    return config


def capabilities(config):
    preset = config.get("preset", "generic")
    return {"lyrics": True, "instrumental": True, "duration": "hint" if preset == "minimax" else True,
            "tempo": "hint", "prompt_limit": 4100 if preset == "elevenlabs" else 2000 if preset == "minimax" else 12000,
            "lyrics_limit": 3500 if preset == "minimax" else 12000, "duration_min": 5, "duration_max": 600}


def _pretty(raw):
    """Round 33 (r33c-models-lists): the readable name every MemuAI list uses (app/model_names.py)."""
    try:
        from .model_names import pretty
        return pretty(raw) or raw
    except Exception:
        return raw


def models():
    rows = {}
    for account in state.usable_accounts("custom"):
        prefix = str(account.fields.get("name", "")).strip().lower().replace(" ", "-")
        if not prefix or not account.fields.get("music_config"):
            continue
        try:
            config = parse_config(account.fields)
        except ValueError:
            continue
        for raw in model_names(account.fields):
            model_id = prefix + "-" + raw
            entry = state.find_model(model_id)
            if entry is not None and not entry.enabled:
                continue
            rows[model_id] = {"id": model_id, "name": _pretty(raw), "raw_model": raw, "provider": prefix + " · Music",
                "caps": {"music": True, "text": False, "image": False, "vision": False, "search": False, "thinking": False},
                "capabilities": capabilities(config)}
    return list(rows.values())


def is_music_model(model_id, *, resolved=None):
    """Classify declared music routes even when their account/model is disabled."""
    resolved = resolved or resolve(str(model_id or ""))
    if resolved is None or resolved.provider != "custom":
        return False
    return any(account.provider == "custom" and account.fields.get("music_config")
               and str(account.fields.get("name", "")).strip().lower().replace(" ", "-") == resolved.prefix
               and resolved.model in model_names(account.fields) for account in list(state.accounts.values()))


def _selection(model_id):
    resolved = resolve(str(model_id or ""))
    if resolved is None or resolved.provider != "custom":
        raise ValueError("Выберите музыкальную модель MemuAI Proxy / Choose a MemuAI Proxy music model")
    if resolved.entry is not None and not resolved.entry.enabled:
        raise ValueError("Музыкальная модель отключена / Music model is disabled")
    accounts = [account for account in state.usable_accounts("custom")
        if str(account.fields.get("name", "")).strip().lower().replace(" ", "-") == resolved.prefix
        and resolved.model in model_names(account.fields) and account.fields.get("music_config")]
    accounts.sort(key=lambda account: (account.priority == "reserve", account.order, account.created))
    if not accounts:
        raise ValueError("Для этой модели нет доступного музыкального аккаунта / No usable music account for this model")
    return accounts, resolved.model


def _adapter(config, payload, raw_model):
    values = {**payload, "model": raw_model}
    descriptor = str(payload.get("prompt", "")).strip()
    if not descriptor:
        raise ValueError("Опишите музыку / Describe the music")
    style = str(payload.get("style", "")).strip()
    instrumental = payload.get("instrumental", True)
    if not isinstance(instrumental, bool):
        raise ValueError("instrumental должен быть boolean / instrumental must be a boolean")
    lyrics = "" if instrumental else str(payload.get("lyrics", ""))
    try:
        duration, tempo = int(payload.get("duration", 60)), int(payload.get("tempo", 100))
    except (TypeError, ValueError) as exc:
        raise ValueError("Некорректный темп или длительность / Invalid tempo or duration") from exc
    if not 5 <= duration <= 600 or not 40 <= tempo <= 240 or len(lyrics) > 12000 or len(style) > 2000 or len(descriptor) > 12000:
        raise ValueError("Параметры музыки выходят за допустимые пределы / Music parameters exceed supported limits")
    values.update(duration=duration, tempo=tempo, instrumental=instrumental, lyrics=lyrics)
    preset = config.get("preset", "generic")
    if preset == "generic":
        return {**config, "template": config.get("template", TEMPLATE)}, values
    full_prompt = "\n".join(value for value in (descriptor, "Style: " + style if style else "", f"Tempo: {tempo} BPM") if value)
    if preset == "elevenlabs":
        if lyrics:
            full_prompt += "\nLyrics:\n" + lyrics
        if len(full_prompt) > 4100:
            raise ValueError("ElevenLabs: описание, стиль и текст вместе должны быть не длиннее 4100 символов / Combined prompt, style and lyrics exceed 4100 characters")
        return {**config, "template": {"model_id": "{{model}}", "prompt": full_prompt,
            "music_length_ms": duration * 1000, "force_instrumental": instrumental}}, values
    full_prompt += f"\nTarget duration: {duration} seconds."
    if len(full_prompt) > 2000 or len(lyrics) > 3500:
        raise ValueError("MiniMax: описание ≤2000, текст ≤3500 символов / Prompt limit 2000, lyrics limit 3500")
    return {**config, "audio_path": "data.audio", "base64_path": "", "error_path": "base_resp.status_code",
            "template": {"model": "{{model}}", "prompt": full_prompt, "lyrics": lyrics,
                "is_instrumental": instrumental, "output_format": "url", "stream": False,
                "audio_setting": {"sample_rate": 44100, "bitrate": 256000, "format": "mp3"}}}, values


def _error_text(exc):
    """«API HTTP 422» plus the reason the music API gave in its body (ElevenLabs:
    ``{"detail": {"status": "bad_prompt", "message": …, "data": {"prompt_suggestion": …}}}`` or a
    validation list) — the bare status code told the user nothing."""
    message = str(exc)
    body = str(getattr(exc, "body", "") or "")
    try:
        detail = json.loads(body).get("detail") if body else None
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, dict):
        code = str(detail.get("code") or detail.get("status") or "")
        data = detail.get("data") if isinstance(detail.get("data"), dict) else {}
        suggestion = str(data.get("prompt_suggestion") or "").strip()[:600]
        if code in ("bad_prompt", "bad_composition_plan"):
            reason = ("описание упоминает исполнителя, группу или чужой текст / the prompt names an artist, a band or "
                      "copyrighted lyrics")
            if suggestion:
                reason += f". Можно так / Try: «{suggestion}»"
        else:
            reason = str(detail.get("message") or code)[:400]
    elif isinstance(detail, list):
        reason = "; ".join(str(row.get("msg") or "")[:200] for row in detail[:3] if isinstance(row, dict))
    else:
        reason = str(detail or "")[:400]
    return f"{message}: {reason}" if reason.strip() else message


async def generate(payload, *, guard=None):
    if guard is not None:
        guard()
    accounts, raw_model = _selection(payload.get("model"))
    from .providers import get
    provider = get("custom")
    if provider is None:
        raise ValueError("Музыкальный провайдер недоступен / Music provider is unavailable")
    attempts, last_error = 0, None
    for account in accounts[:8]:
        config, values = _adapter(parse_config(account.fields), payload, raw_model)
        config = {**config, "endpoint": urljoin(str(account.fields["base_url"]).rstrip("/") + "/", str(config.get("endpoint", "audio/music")))}
        try:
            keys = provider._key_candidates(account)
        except Exception:
            last_error = ValueError("Ключи музыкального аккаунта отключены / Music account keys are disabled")
            continue
        for key in keys[:8]:
            if attempts >= 8:
                break
            attempts += 1
            headers = provider._headers(account, api_key="")
            auth = config.get("auth", "bearer")
            if key and auth != "none":
                header = {"bearer": "Authorization", "google": "x-goog-api-key", "anthropic": "x-api-key"}.get(auth, config.get("auth_header", "x-api-key"))
                headers[header] = ("Bearer " if auth == "bearer" else str(config.get("auth_prefix", ""))) + key
            def current():
                if guard is not None:
                    guard()
                if not account.usable() or account not in state.usable_accounts("custom"):
                    raise ValueError("Музыкальный аккаунт отключён / Music account was disabled")
                entry = state.find_model(str(payload.get("model", "")))
                if entry is not None and not entry.enabled:
                    raise ValueError("Музыкальная модель отключена / Music model was disabled")
            started = time.monotonic()
            try:
                result = await module_media.generate_config(config, values, guard=current, headers=headers)
                account.record_result(True, ms=round((time.monotonic() - started) * 1000))
                account.last_used = time.time()
                return {**result, "model": str(payload.get("model")), "provider": str(account.fields.get("name", ""))}
            except asyncio.CancelledError:
                raise
            except PermissionError:
                raise
            except Exception as exc:
                current()
                message = _error_text(exc) if isinstance(exc, ValueError) else type(exc).__name__
                account.record_result(False, ms=round((time.monotonic() - started) * 1000), error=message)
                if isinstance(exc, module_media.MusicStartError) and (exc.status in (401, 403, 429) or 500 <= exc.status <= 599):
                    matched_key_failure = provider._matches_key_failure(account, message) and bool(key)
                    if matched_key_failure:
                        provider._disable_key(account, key)
                    last_error = ValueError(message)
                    if matched_key_failure:
                        continue
                    break
                raise ValueError(message) from exc
    raise last_error or ValueError("Нет доступного музыкального аккаунта / No usable music account")


async def run(payload, *, scope="api"):
    request_id = str(payload.get("request_id") or uuid.uuid4().hex)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request_id):
        raise ValueError("Некорректный request_id / Invalid request_id")
    identity = (scope, request_id)
    if payload.get("action") == "cancel":
        task = _JOBS.get(identity)
        if task:
            task.cancel()
        return {"ok": True, "cancelled": bool(task)}
    if identity in _JOBS or len(_JOBS) >= 32:
        raise ValueError("Музыкальная генерация уже выполняется или очередь заполнена / Music generation is running or the queue is full")
    task = asyncio.create_task(generate(payload))
    _JOBS[identity] = task
    try:
        return await task
    finally:
        _JOBS.pop(identity, None)
