"""Configurable music API transport and durable voice clips.

Music providers use different schemas. A saved request template and explicit
result/job paths adapt those schemas without embedding any provider in Studio.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from . import modules_host, pgdata
from .module_monitor import field, public_request, require
from .storage import load_json_with_recovery, mutate_json

MAX_AUDIO = 24 * 1024 * 1024
MIMES = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/mpeg": "mp3", "audio/mp3": "mp3",
         "audio/ogg": "ogg", "audio/webm": "webm", "audio/mp4": "m4a", "audio/flac": "flac"}

class MusicStartError(ValueError):
    """Explicit HTTP rejection before the provider accepted a music job."""
    def __init__(self, message, status, body=""):
        super().__init__(message)
        self.status = status
        self.body = body

def _audio_mime(raw):
    return ("audio/wav" if raw[:4] == b"RIFF" else "audio/ogg" if raw[:4] == b"OggS"
            else "audio/flac" if raw[:4] == b"fLaC" else "audio/webm" if raw[:4] == b"\x1aE\xdf\xa3"
            else "audio/mp4" if raw[4:8] == b"ftyp" else "audio/mpeg")

def decode_audio(data):
    match = re.fullmatch(r"data:(audio/[\w.+-]+)(?:;[^,]*)?;base64,([A-Za-z0-9+/=\s]+)", str(data))
    if not match or match[1] not in MIMES or len(match[2]) > MAX_AUDIO * 4 // 3 + 100:
        raise ValueError("Некорректная аудиозапись / Invalid audio recording")
    raw = base64.b64decode(match[2], validate=False)
    if not raw or len(raw) > MAX_AUDIO:
        raise ValueError("Аудиозапись превышает 24 МБ / Recording exceeds 24 MB")
    mime = match[1]
    signatures = {"wav": raw[:4] == b"RIFF" and raw[8:12] == b"WAVE", "ogg": raw[:4] == b"OggS",
                  "webm": raw[:4] == b"\x1aE\xdf\xa3", "flac": raw[:4] == b"fLaC",
                  "m4a": raw[4:8] == b"ftyp", "mp3": raw[:3] == b"ID3" or (len(raw) > 1 and raw[0] == 255 and raw[1] & 224 == 224)}
    if not signatures[MIMES[mime]]:
        raise ValueError("Формат аудиофайла не совпадает / Audio file format mismatch")
    return raw, mime

def clip_folder(profile_id=None):
    return pgdata.MEMU_DIR / pgdata._profile_data_name("voice_clips", profile_id)

def save_clip(data, *, profile_id=None, duration=0, truncated=False):
    raw, mime = decode_audio(data)
    folder = clip_folder(profile_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(raw).hexdigest() + "." + MIMES[mime]
    target = folder / name
    if not target.exists():
        handle, temporary = tempfile.mkstemp(prefix=".clip-", dir=folder)
        try:
            with os.fdopen(handle, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    active = profile_id or pgdata.load_profiles().get("active_id", "default")
    return {"url": f"/api/voice/clips/{name}?profile_id={active}", "mime": mime,
            "duration": max(0, min(3600, float(duration or 0))), "size": len(raw), "truncated": bool(truncated)}

def music_config(module_id, payload=None):
    require(module_id, "media")
    path = modules_host.default_root() / "media-connections.json"
    if payload is not None:
        config = {key: payload[key] for key in ("endpoint", "template", "audio_path", "base64_path",
                  "job_id_path", "poll_url", "status_path", "ready_statuses", "failed_statuses", "auth", "model") if key in payload}
        endpoint = str(config.get("endpoint", ""))
        if not endpoint.startswith("https://") or urlsplit(endpoint).username or urlsplit(endpoint).password:
            raise ValueError("Для музыкального API нужен HTTPS / Music API requires HTTPS")
        if not isinstance(config.get("template", {}), dict) or len(json.dumps(config)) > 24000:
            raise ValueError("Некорректный шаблон запроса / Invalid request template")
        def change(data):
            previous = data.get(module_id, {})
            config["api_key"] = str(payload["api_key"])[:4000] if "api_key" in payload else (
                previous.get("api_key", "") if endpoint == previous.get("endpoint") else "")
            data[module_id] = config
            return True
        mutate_json(str(path), {}, change, backup=False)
    config = load_json_with_recovery(str(path), {}).get(module_id, {})
    return {**{key: value for key, value in config.items() if key != "api_key"}, "key_saved": bool(config.get("api_key"))}

def _substitute(obj, values):
    if isinstance(obj, dict):
        return {key: _substitute(value, values) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_substitute(value, values) for value in obj]
    if isinstance(obj, str):
        exact = re.fullmatch(r"\{\{(\w+)\}\}", obj)
        if exact:
            return values.get(exact[1], "")
        return re.sub(r"\{\{(\w+)\}\}", lambda match: str(values.get(match[1], "")), obj)
    return obj

async def generate_music(module_id, payload):
    require(module_id, "media", enabled=True)
    path = modules_host.default_root() / "media-connections.json"
    config = load_json_with_recovery(str(path), {}).get(module_id, {})
    if not config.get("endpoint"):
        raise ValueError("Сначала подключите музыкальный API / Connect a music API first")
    return await generate_config(config, payload, guard=lambda: require(module_id, "media", enabled=True))

async def generate_config(config, payload, *, guard=lambda: None, headers=None):
    """Shared bounded transport for Proxy adapters and retained legacy connections."""
    guard()
    proxy_headers = headers is not None
    values = {"prompt": str(payload.get("prompt", ""))[:12000], "lyrics": str(payload.get("lyrics", ""))[:12000],
              "style": str(payload.get("style", ""))[:2000], "model": str(payload.get("model") or config.get("model", ""))[:300],
              "duration": max(5, min(600, int(payload.get("duration", 60)))),
              "tempo": max(40, min(240, int(payload.get("tempo", 100)))), "instrumental": bool(payload.get("instrumental", True))}
    request = _substitute(config.get("template", {"model": "{{model}}", "prompt": "{{prompt}}", "duration": "{{duration}}"}), values)
    from .module_monitor import _headers
    if headers is None:
        headers = _headers(config) if config.get("api_key") else None
    try:

        raw, mime, final_url = await public_request(config["endpoint"], method="POST", headers=headers, payload=request, limit=MAX_AUDIO,
                                                    timeout=600 if config.get("preset") == "elevenlabs" else 180)
    except ValueError as exc:
        match = re.fullmatch(r"API HTTP (\d{3})", str(exc))
        if match:
            raise MusicStartError(str(exc), int(match[1]), str(getattr(exc, "body", "") or "")) from exc
        raise
    finally:
        guard()
    deadline, job = time.monotonic() + 600, ""
    while True:
        guard()
        if mime.split(";")[0] in MIMES:
            data = f"data:{mime.split(';')[0]};base64," + base64.b64encode(raw).decode("ascii")
            decode_audio(data)
            return {"audio": data, "mime": mime.split(";")[0]}
        try:
            obj = json.loads(raw)
        except ValueError as exc:
            raise ValueError("Музыкальный API вернул неизвестный формат / Unknown music API response") from exc
        error_code = field(obj, config.get("error_path")) if config.get("error_path") else None
        if error_code not in (None, "", 0, "0"):
            raise ValueError(f"Музыкальный API отклонил запрос (код {str(error_code)[:40]}) / Music API rejected the request")
        audio_path, base64_path = config.get("audio_path", "data.0.url"), config.get("base64_path", "data.0.b64_json")
        audio = field(obj, audio_path) if audio_path else None
        encoded = field(obj, base64_path) if base64_path else None
        hexadecimal = field(obj, config.get("hex_path")) if config.get("hex_path") else None
        if hexadecimal:
            if not isinstance(hexadecimal, str) or len(hexadecimal) > MAX_AUDIO * 2:
                raise ValueError("Некорректное аудио / Invalid audio")
            try:
                decoded = bytes.fromhex(hexadecimal)
            except ValueError as exc:
                raise ValueError("Некорректное hex аудио / Invalid hex audio") from exc
            data = "data:" + _audio_mime(decoded) + ";base64," + base64.b64encode(decoded).decode("ascii")
            _, audio_mime = decode_audio(data)
            return {"audio": data, "mime": audio_mime}
        if encoded:
            encoded = str(encoded)
            if encoded.startswith("data:audio/"):
                data = encoded
            else:
                if len(encoded) > MAX_AUDIO * 4 // 3 + 100:
                    raise ValueError("Аудиозапись превышает 24 МБ / Recording exceeds 24 MB")
                data = "data:" + _audio_mime(base64.b64decode(encoded)) + ";base64," + encoded
            _, audio_mime = decode_audio(data)
            return {"audio": data, "mime": audio_mime}
        if isinstance(audio, str) and audio:
            if audio.startswith("data:audio/"):
                _, audio_mime = decode_audio(audio)
                return {"audio": audio, "mime": audio_mime}
            audio_url = urljoin(final_url, audio)
            audio_headers = headers if proxy_headers and urlsplit(audio_url)[:2] == urlsplit(config["endpoint"])[:2] else None
            guard()
            try:
                raw, mime, _ = await public_request(audio_url, **({"headers": audio_headers} if proxy_headers else {}), limit=MAX_AUDIO)
            finally:
                guard()
            if mime.split(";")[0] not in MIMES:

                mime = _audio_mime(raw)
            data = f"data:{mime.split(';')[0]};base64," + base64.b64encode(raw).decode("ascii")
            decode_audio(data)
            return {"audio": data, "mime": mime.split(";")[0]}
        status = str(field(obj, config.get("status_path", "status"), ""))
        if status in config.get("failed_statuses", ["failed", "error", "cancelled"]):
            raise ValueError("Музыкальный API отклонил генерацию / Music API generation failed")
        job = job or str(field(obj, config.get("job_id_path", "id"), ""))
        poll = str(config.get("poll_url", ""))
        if not job or not poll or time.monotonic() >= deadline:
            raise ValueError("Аудио не получено; проверьте пути результата и опроса / No audio; check result and polling paths")
        if status in config.get("ready_statuses", ["completed", "succeeded", "done"]):
            raise ValueError("Задание готово, но путь аудио неверен / Job ready but audio path is incorrect")
        from urllib.parse import quote
        url = urljoin(config["endpoint"], poll.replace("{id}", quote(job, safe="")))
        if urlsplit(url)[:2] != urlsplit(config["endpoint"])[:2]:
            raise ValueError("Опрос задания должен использовать тот же API / Polling must use the same API origin")
        guard()
        try:
            await asyncio.sleep(3)
        finally:
            guard()
        try:
            raw, mime, final_url = await public_request(url, headers=headers, limit=MAX_AUDIO)
        finally:
            guard()
