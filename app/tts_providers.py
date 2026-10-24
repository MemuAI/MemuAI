"""Cloud text-to-speech providers with their own official request formats.

The "API" voice source used to send every request to ``{base}/audio/speech``
(OpenAI format).  A Google AI Studio URL therefore produced Google's HTML 404
page, which was shown verbatim in the UI.  The service is now detected from
the configured Base URL, each service gets its native endpoint, long text is
split to the provider's input limit, and HTTP errors become short readable
messages.  Any other URL keeps the OpenAI-compatible behaviour, so saved
settings (including local servers such as Kokoro) work without migration.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import re
import urllib.parse
import wave
from collections import OrderedDict
from typing import Any
from xml.sax.saxutils import escape as xml_escape

import httpx

from .version import APP_VERSION

MAX_AUDIO_BYTES = 50 * 1024 * 1024
_TIMEOUT = httpx.Timeout(120, connect=15)

OPENAI_VOICES = ("alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx",
                 "sage", "shimmer", "verse", "marin", "cedar")
# https://ai.google.dev/gemini-api/docs/speech-generation#voices
GEMINI_VOICES = (
    ("Zephyr", "Bright"), ("Puck", "Upbeat"), ("Charon", "Informative"), ("Kore", "Firm"),
    ("Fenrir", "Excitable"), ("Leda", "Youthful"), ("Orus", "Firm"), ("Aoede", "Breezy"),
    ("Callirrhoe", "Easy-going"), ("Autonoe", "Bright"), ("Enceladus", "Breathy"), ("Iapetus", "Clear"),
    ("Umbriel", "Easy-going"), ("Algieba", "Smooth"), ("Despina", "Smooth"), ("Erinome", "Clear"),
    ("Algenib", "Gravelly"), ("Rasalgethi", "Informative"), ("Laomedeia", "Upbeat"), ("Achernar", "Soft"),
    ("Alnilam", "Firm"), ("Schedar", "Even"), ("Gacrux", "Mature"), ("Pulcherrima", "Forward"),
    ("Achird", "Friendly"), ("Zubenelgenubi", "Casual"), ("Vindemiatrix", "Gentle"), ("Sadachbia", "Lively"),
    ("Sadaltager", "Knowledgeable"), ("Sulafat", "Warm"),
)
_GEMINI_BY_LOWER = {name.lower(): name for name, _ in GEMINI_VOICES}

# Public descriptors: the settings UI renders its provider dropdown, model and
# voice suggestions and "where to get a key" links from this one table.
PROVIDERS: dict[str, dict[str, Any]] = {
    "openai": {
        "label": "OpenAI", "base_url": "https://api.openai.com/v1",
        "key_url": "https://platform.openai.com/api-keys",
        "docs_url": "https://platform.openai.com/docs/guides/text-to-speech",
        "models": ["gpt-4o-mini-tts", "tts-1", "tts-1-hd"], "default_model": "gpt-4o-mini-tts",
        "voices": [{"id": v, "label": v} for v in OPENAI_VOICES], "default_voice": "alloy",
        "chunk": 4000, "speed": True,
    },
    "gemini": {
        "label": "Google Gemini (AI Studio)", "base_url": "https://generativelanguage.googleapis.com",
        "key_url": "https://aistudio.google.com/apikey",
        "docs_url": "https://ai.google.dev/gemini-api/docs/speech-generation",
        "models": ["gemini-3.8-flash-lite-tts", "gemini-3.8-flash-tts", "gemini-3.1-flash-tts-preview",
                   "gemini-2.5-pro-preview-tts"],
        "default_model": "gemini-3.8-flash-lite-tts",
        "voices": [{"id": name, "label": f"{name} — {style}"} for name, style in GEMINI_VOICES],
        "default_voice": "Kore", "chunk": 3000, "speed": False,
    },
    "elevenlabs": {
        "label": "ElevenLabs", "base_url": "https://api.elevenlabs.io",
        "key_url": "https://elevenlabs.io/app/settings/api-keys",
        "docs_url": "https://elevenlabs.io/docs/api-reference/text-to-speech/convert",
        "models": ["eleven_multilingual_v2", "eleven_v3", "eleven_flash_v2_5", "eleven_turbo_v2_5"],
        "default_model": "eleven_multilingual_v2",
        "voices": [{"id": "JBFqnCBsd6RMkjVDRZzb", "label": "George"}, {"id": "21m00Tcm4TlvDq8ikWAM", "label": "Rachel"},
                   {"id": "EXAVITQu4vr4xnSDxMaL", "label": "Sarah"}],
        "default_voice": "JBFqnCBsd6RMkjVDRZzb", "chunk": 4500, "speed": False,
    },
    "azure": {
        "label": "Microsoft Azure Speech", "base_url": "https://westeurope.tts.speech.microsoft.com",
        "key_url": "https://portal.azure.com/#create/Microsoft.CognitiveServicesSpeechServices",
        "docs_url": "https://learn.microsoft.com/azure/ai-services/speech-service/rest-text-to-speech",
        "models": [], "default_model": "",
        "voices": [{"id": v, "label": v} for v in (
            "ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural", "ru-RU-DariyaNeural", "en-US-AvaMultilingualNeural",
            "en-US-AndrewMultilingualNeural", "en-US-JennyNeural", "en-GB-SoniaNeural", "de-DE-KatjaNeural")],
        "default_voice": "ru-RU-SvetlanaNeural", "chunk": 3000, "speed": True,
    },
    "custom": {
        "label": "OpenAI-compatible", "base_url": "", "key_url": "", "docs_url": "",
        "models": ["tts-1", "gpt-4o-mini-tts", "kokoro"], "default_model": "tts-1",
        "voices": [{"id": v, "label": v} for v in OPENAI_VOICES], "default_voice": "alloy",
        "chunk": 4000, "speed": True,
    },
}


class TTSApiError(RuntimeError):
    """A readable provider/configuration error; never contains HTML or secrets."""


def _t(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru


def provider_for_url(url: object) -> str:
    """Which service a Base URL belongs to (unknown hosts are OpenAI-compatible)."""
    host = (urllib.parse.urlsplit(str(url or "").strip()).hostname or "").lower()
    if host == "api.openai.com":
        return "openai"
    if host == "generativelanguage.googleapis.com":
        return "gemini"
    if host == "elevenlabs.io" or host.endswith(".elevenlabs.io"):
        return "elevenlabs"
    if host.endswith((".tts.speech.microsoft.com", ".tts.speech.azure.us")):
        return "azure"
    return "custom"


def api_root(base: str) -> str:
    """The API root from a Base URL the user may have pasted as a full endpoint
    (``…/v1/audio/speech`` used to become ``…/v1/audio/speech/audio/speech`` → 404 «model not found»)."""
    return re.sub(r"/audio/(?:speech|transcriptions|translations)$", "", base, flags=re.I)


def public_providers() -> list[dict[str, Any]]:
    keys = ("label", "base_url", "key_url", "docs_url", "models", "default_model", "voices", "default_voice", "speed")
    return [{"id": pid, **{k: meta[k] for k in keys}} for pid, meta in PROVIDERS.items()]


def speed_from_rate(rate: object) -> float:
    """Windows-style rate −10…10 → playback speed 0.5…2.0 (0 → 1.0)."""
    try:
        value = max(-10, min(10, int(rate)))
    except (TypeError, ValueError):
        value = 0
    return round(2 ** (value / 10), 2)


def split_text(text: str, limit: int) -> list[str]:
    """Split at paragraph, then sentence, then word boundaries, each piece ≤ ``limit``."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    pieces = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    units: list[str] = []
    for piece in pieces:
        if len(piece) <= limit:
            units.append(piece)
            continue
        for sentence in re.split(r"(?<=[.!?…;:])\s+", piece):
            while len(sentence) > limit:
                cut = sentence.rfind(" ", 0, limit)
                cut = cut if cut > limit // 2 else limit
                units.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            if sentence:
                units.append(sentence)
    chunks: list[str] = []
    for unit in units:
        separator = "\n\n" if unit in pieces else " "
        if chunks and len(chunks[-1]) + len(separator) + len(unit) <= limit:
            chunks[-1] += separator + unit
        else:
            chunks.append(unit)
    return chunks


def check_settings(kind: str, model: str, voice: str, lang: str = "ru") -> tuple[str, str]:
    """Normalize model/voice and reject combinations that can never work."""
    meta = PROVIDERS[kind]
    model = str(model or "").strip() or meta["default_model"]
    voice = str(voice or "").strip() or meta["default_voice"]
    label = meta["label"]
    if kind == "gemini":
        model = model.removeprefix("models/")
        if "tts" not in model.lower():
            raise TTSApiError(_t(lang,
                f"Модель «{model}» не умеет озвучивать текст. Выберите TTS-модель Gemini, например {meta['default_model']}.",
                f"Model “{model}” cannot synthesize speech. Choose a Gemini TTS model such as {meta['default_model']}."))
        if voice.lower() in OPENAI_VOICES:
            raise TTSApiError(_t(lang,
                f"Голос «{voice}» есть только у OpenAI. У Gemini свои голоса: Kore, Puck, Zephyr, Charon… — выберите один из списка.",
                f"“{voice}” is an OpenAI voice. Gemini has its own voices: Kore, Puck, Zephyr, Charon… — pick one from the list."))
        voice = _GEMINI_BY_LOWER.get(voice.lower(), voice)
    elif kind == "openai":
        if voice.lower() in _GEMINI_BY_LOWER:
            raise TTSApiError(_t(lang,
                f"Голос «{voice}» есть только у Gemini. У OpenAI: {', '.join(OPENAI_VOICES[:8])}…",
                f"“{voice}” is a Gemini voice. OpenAI voices: {', '.join(OPENAI_VOICES[:8])}…"))
        voice = voice.lower() if voice.lower() in OPENAI_VOICES else voice
    elif kind == "elevenlabs":
        if voice.lower() in OPENAI_VOICES or voice.lower() in _GEMINI_BY_LOWER:
            raise TTSApiError(_t(lang,
                f"Для {label} нужен voice_id из библиотеки голосов (например JBFqnCBsd6RMkjVDRZzb), а не «{voice}».",
                f"{label} needs a voice_id from your voice library (e.g. JBFqnCBsd6RMkjVDRZzb), not “{voice}”."))
    elif kind == "azure":
        if not re.fullmatch(r"[a-z]{2,3}-[A-Za-z0-9]{2,8}(?:-[A-Za-z0-9]+)+", voice):
            raise TTSApiError(_t(lang,
                f"Голос Azure указывается полностью, например ru-RU-SvetlanaNeural, а не «{voice}».",
                f"Azure voices are full names such as ru-RU-SvetlanaNeural, not “{voice}”."))
    return model, voice


def _origin(base: str) -> str:
    parts = urllib.parse.urlsplit(base)
    return f"{parts.scheme}://{parts.netloc}"


def build_request(kind: str, base: str, api_key: str, model: str, voice: str, text: str,
                  speed: float = 1.0) -> dict[str, Any]:
    """httpx request kwargs (method/url/headers/json|content) for one chunk."""
    key = api_key.strip()
    if kind == "gemini":
        url = f"{_origin(base)}/v1beta/models/{urllib.parse.quote(model, safe='.-_')}:generateContent"
        body = {"contents": [{"role": "user", "parts": [{"text": text}]}],
                "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
        return {"method": "POST", "url": url, "headers": {"x-goog-api-key": key}, "json": body}
    if kind == "elevenlabs":
        url = f"{_origin(base)}/v1/text-to-speech/{urllib.parse.quote(voice, safe='')}?output_format=mp3_44100_128"
        return {"method": "POST", "url": url, "headers": {"xi-api-key": key, "Accept": "audio/mpeg"},
                "json": {"text": text, "model_id": model}}
    if kind == "azure":
        parts = urllib.parse.urlsplit(base)
        path = parts.path.rstrip("/")
        url = f"{_origin(base)}{path if path.endswith('/cognitiveservices/v1') else path + '/cognitiveservices/v1'}"
        locale = "-".join(voice.split("-")[:2])
        spoken = xml_escape(text)
        if abs(speed - 1.0) > 0.01:
            spoken = f"<prosody rate='{round((speed - 1) * 100):+d}%'>{spoken}</prosody>"
        ssml = (f"<speak version='1.0' xml:lang='{xml_escape(locale)}'><voice name='{xml_escape(voice)}'>"
                f"{spoken}</voice></speak>")
        return {"method": "POST", "url": url, "content": ssml.encode("utf-8"),
                "headers": {"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/ssml+xml",
                            "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3",
                            "User-Agent": f"MemuAI/{APP_VERSION}"}}
    body: dict[str, Any] = {"model": model, "voice": voice, "input": text, "response_format": "mp3"}
    if abs(speed - 1.0) > 0.01:
        body["speed"] = speed
    headers = {"Authorization": "Bearer " + key} if key else {}
    return {"method": "POST", "url": base + "/audio/speech", "headers": headers, "json": body}


def sniff_mime(data: bytes, declared: str = "") -> str:
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    if data[:4] == b"OggS":
        return "audio/ogg"
    if data[:4] == b"fLaC":
        return "audio/flac"
    declared = declared.split(";", 1)[0].strip().lower()
    return declared if declared.startswith("audio/") else ""


def pcm_to_wav(pcm: bytes, rate: int = 24000, channels: int = 1, width: int = 2) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return out.getvalue()


def _gemini_audio(payload: Any, lang: str) -> tuple[bytes, str] | None:
    """Extract audio from generateContent JSON; None when the model sent no audio."""
    if not isinstance(payload, dict):
        return None
    candidates = payload.get("candidates") or []
    if not candidates:
        reason = (payload.get("promptFeedback") or {}).get("blockReason")
        if reason:
            raise TTSApiError(_t(lang, f"Google отклонил текст фильтром безопасности ({reason}).",
                                 f"Google blocked the text with a safety filter ({reason})."))
        return None
    for part in ((candidates[0].get("content") or {}).get("parts") or []):
        inline = part.get("inlineData") or part.get("inline_data") or {}
        data = inline.get("data")
        if not data:
            continue
        try:
            raw = base64.b64decode(data, validate=False)
        except (binascii.Error, ValueError):
            return None
        mime = str(inline.get("mimeType") or inline.get("mime_type") or "").lower()
        if raw[:4] == b"RIFF" or "wav" in mime:
            return raw, "audio/wav"
        rate_match = re.search(r"rate=(\d+)", mime)
        rate = int(rate_match.group(1)) if rate_match else 24000
        if "l16" in mime or "pcm" in mime or not mime:
            return pcm_to_wav(raw, rate), "audio/wav"
        return raw, mime.split(";", 1)[0]
    return None


def _strip_id3(data: bytes) -> bytes:
    if data[:3] != b"ID3" or len(data) < 10:
        return data
    size = (data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F)
    return data[10 + size:]


def merge_audio(parts: list[tuple[bytes, str]], lang: str = "ru") -> tuple[bytes, str]:
    if len(parts) == 1:
        return parts[0]
    mimes = {mime for _, mime in parts}
    if mimes == {"audio/mpeg"}:
        return parts[0][0] + b"".join(_strip_id3(data) for data, _ in parts[1:]), "audio/mpeg"
    if mimes == {"audio/wav"}:
        frames: list[bytes] = []
        formats = set()
        try:
            for data, _ in parts:
                with wave.open(io.BytesIO(data), "rb") as reader:
                    formats.add((reader.getnchannels(), reader.getsampwidth(), reader.getframerate()))
                    frames.append(reader.readframes(reader.getnframes()))
        except (wave.Error, EOFError) as exc:
            raise TTSApiError(_t(lang, "Сервис вернул повреждённый WAV", "The service returned a damaged WAV")) from exc
        if len(formats) == 1:
            channels, width, rate = formats.pop()
            return pcm_to_wav(b"".join(frames), rate, channels, width), "audio/wav"
    raise TTSApiError(_t(lang, "Сервис вернул части аудио в разных форматах — сократите текст.",
                         "The service returned audio parts in different formats — shorten the text."))


def _clean_message(raw: str, secret: str = "") -> str:
    """Provider error text without markup, whitespace runs or the user's key."""
    if re.search(r"<\s*(!doctype|html|head|body)\b", raw, re.I):
        return ""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()
    if secret and len(secret) >= 6:
        text = text.replace(secret, "•••")
    return text[:280]


def _payload_message(payload: Any) -> str:
    if isinstance(payload, dict):
        error = payload.get("error", payload.get("detail", payload))
        if isinstance(error, dict):
            return str(error.get("message") or error.get("detail") or error.get("status") or "")
        if isinstance(error, list) and error and isinstance(error[0], dict):
            return str(error[0].get("msg") or error[0].get("message") or "")
        return str(error) if isinstance(error, str) else ""
    return ""


def error_message(kind: str, status: int, body: bytes, *, model: str = "", voice: str = "",
                  lang: str = "ru", secret: str = "", endpoint: str = "/audio/speech") -> str:
    """Short human message for a failed provider response (no HTML is ever passed through)."""
    meta = PROVIDERS.get(kind, PROVIDERS["custom"])
    label = meta["label"] if kind != "custom" else "API"
    raw = body.decode("utf-8", "replace") if body else ""
    try:
        detail = _clean_message(_payload_message(json.loads(raw)), secret)
    except ValueError:
        detail = _clean_message(raw, secret)
    models = ", ".join(meta["models"][:4]) if endpoint == "/audio/speech" else ""
    low = detail.lower()
    if "quota_exceeded" in low or "exceeds your quota" in low or "insufficient_quota" in low:
        # ElevenLabs reports a spent character quota as HTTP 401 — not a wrong key.
        human = _t(lang, "закончилась квота (кредиты) сервиса — пополните баланс или подождите новый период",
                   "the service quota (credits) is used up — top up or wait for the next period")
    elif status == 401 or "api key not valid" in low or "invalid api key" in low or "incorrect api key" in low:
        human = _t(lang, "неверный или пустой API-ключ — проверьте ключ этого сервиса",
                   "invalid or missing API key — check the key for this service")
    elif status == 403:
        human = _t(lang, "доступ запрещён: ключ не подходит для этой модели, региона или тарифа",
                   "access denied: the key is not allowed for this model, region or plan")
    elif status == 404:
        if kind == "custom":
            human = _t(lang, f"адрес не найден — проверьте Base URL: сервер должен поддерживать {endpoint}",
                       f"endpoint not found — check the Base URL: the server must support {endpoint}")
        elif kind == "elevenlabs" and "voice" in low:
            human = _t(lang, f"голос «{voice}» не найден — укажите voice_id из своей библиотеки",
                       f"voice “{voice}” not found — use a voice_id from your library")
        else:
            human = _t(lang, f"модель «{model}» не найдена — проверьте название" + (f". Доступные: {models}" if models else ""),
                       f"model “{model}” not found — check the name" + (f". Available: {models}" if models else ""))
    elif status == 429:
        human = _t(lang, "превышен лимит запросов или закончилась квота — подождите и повторите",
                   "rate limit or quota exceeded — wait and try again")
    elif status == 413:
        human = _t(lang, "текст слишком длинный для одного запроса", "the text is too long for one request")
    elif status in (400, 422):
        if "voice" in low:
            human = _t(lang, f"голос «{voice}» не подходит для этой модели — выберите голос из списка",
                       f"voice “{voice}” does not fit this model — pick a voice from the list")
        elif "model" in low and kind != "azure":
            human = _t(lang, f"модель «{model}» не подходит" + (f". Доступные: {models}" if models else ""),
                       f"model “{model}” is not supported" + (f". Available: {models}" if models else ""))
        else:
            human = _t(lang, "сервис отклонил запрос — проверьте модель и голос",
                       "the service rejected the request — check the model and voice")
    elif status >= 500:
        human = _t(lang, f"сервис временно недоступен (HTTP {status}) — повторите позже",
                   f"the service is temporarily unavailable (HTTP {status}) — try again later")
    else:
        human = _t(lang, f"ошибка HTTP {status}", f"HTTP error {status}")
    message = f"{label}: {human}"
    if detail and detail.lower() not in human.lower():
        message += f" ({detail})"
    return message


def trusts_env(base: str) -> bool:
    """A TTS/STT server on this computer is never reached through the system/VPN proxy (httpx takes the env and
    Windows-registry proxy even for 127.0.0.1, and the proxy answers 404/502 for a local Kokoro/whisper server)."""
    host = (urllib.parse.urlsplit(base).hostname or "").lower()
    return not (host in ("localhost", "::1") or host.startswith("127.") or host.endswith(".localhost"))


def connection_message(kind: str, base: str, exc: Exception, lang: str = "ru", stt: bool = False) -> str:
    host = urllib.parse.urlsplit(base).hostname or base
    if isinstance(exc, httpx.TimeoutException):
        return _t(lang, f"{host} не ответил вовремя — проверьте интернет или "
                        + ("сократите запись" if stt else "сократите текст"),
                  f"{host} did not answer in time — check the connection or shorten the "
                  + ("recording" if stt else "text"))
    if kind == "custom" and host in ("127.0.0.1", "localhost", "::1"):
        # Распознавание тоже ходит сюда: «запустите TTS-сервер» при сбое STT сбивало с толку.
        return _t(lang, f"Не удалось подключиться к {host}: запустите локальный "
                        + ("сервер распознавания речи" if stt else "TTS-сервер"),
                  f"Could not connect to {host}: start the local "
                  + ("speech recognition server" if stt else "TTS server"))
    return _t(lang, f"Не удалось подключиться к {host} — проверьте интернет и Base URL",
              f"Could not connect to {host} — check the connection and the Base URL")


async def _send(client: httpx.AsyncClient, request: dict[str, Any], lang: str = "ru") -> tuple[int, str, bytes]:
    """Send with a running size limit (a huge body is never buffered whole)."""
    method, url = request["method"], request["url"]
    kwargs = {k: v for k, v in request.items() if k not in ("method", "url")}
    async with client.stream(method, url, **kwargs) as response:
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > MAX_AUDIO_BYTES:
                raise TTSApiError(_t(lang, "API вернул слишком большой ответ", "The API returned a response that is too large"))
        return response.status_code, response.headers.get("content-type", ""), bytes(content)


class _Cache:
    """Last synthesized clips: replaying a message does not pay for it twice."""

    def __init__(self, entries: int = 12, total: int = 30 * 1024 * 1024) -> None:
        self.entries, self.total = entries, total
        self.items: OrderedDict[str, tuple[bytes, str]] = OrderedDict()

    def get(self, key: str) -> tuple[bytes, str] | None:
        item = self.items.get(key)
        if item is not None:
            self.items.move_to_end(key)
        return item

    def put(self, key: str, value: tuple[bytes, str]) -> None:
        if len(value[0]) > self.total // 2:
            return
        self.items[key] = value
        self.items.move_to_end(key)
        while len(self.items) > self.entries or sum(len(v[0]) for v in self.items.values()) > self.total:
            self.items.popitem(last=False)


CACHE = _Cache()


async def synthesize(text: str, *, base_url: str, api_key: str, model: str, voice: str,
                     speed: float = 1.0, lang: str = "ru",
                     transport: httpx.AsyncBaseTransport | None = None) -> tuple[bytes, str]:
    """Speak ``text`` with the service behind ``base_url`` → (audio bytes, mime)."""
    from .voice_runtime import validate_api_base_url
    base = api_root(validate_api_base_url(base_url))
    kind = provider_for_url(base)
    meta = PROVIDERS[kind]
    model, voice = check_settings(kind, model, voice, lang)
    if kind != "custom" and not api_key.strip():
        raise TTSApiError(_t(lang, f"{meta['label']}: укажите API-ключ. Получить его: {meta['key_url']}",
                             f"{meta['label']}: enter an API key. Get one at {meta['key_url']}"))
    speed = speed if meta["speed"] else 1.0
    cache_key = hashlib.sha256(json.dumps([kind, base, model, voice, speed, text,
                                           hashlib.sha256(api_key.encode()).hexdigest()]).encode()).hexdigest()
    cached = CACHE.get(cache_key)
    if cached:
        return cached
    chunks = split_text(text, meta["chunk"])
    if not chunks:
        raise TTSApiError(_t(lang, "Нет текста для озвучивания", "There is no text to speak"))
    parts: list[tuple[bytes, str]] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport, trust_env=trusts_env(base)) as client:
        for chunk in chunks:
            request = build_request(kind, base, api_key, model, voice, chunk, speed)
            # Gemini TTS occasionally answers 500/no-audio for a valid request: one retry.
            for attempt in range(2 if kind == "gemini" else 1):
                try:
                    status, ctype, body = await _send(client, request, lang)
                except httpx.HTTPError as exc:
                    raise TTSApiError(connection_message(kind, base, exc, lang)) from exc
                if status >= 400:
                    if attempt == 0 and kind == "gemini" and status in (500, 503):
                        continue
                    raise TTSApiError(error_message(kind, status, body, model=model, voice=voice,
                                                    lang=lang, secret=api_key.strip()))
                if kind == "gemini":
                    try:
                        audio = _gemini_audio(json.loads(body), lang)
                    except ValueError:
                        audio = None
                    if audio is None:
                        if attempt == 0:
                            continue
                        raise TTSApiError(_t(lang,
                            f"Gemini не вернул звук — проверьте, что «{model}» является TTS-моделью.",
                            f"Gemini returned no audio — make sure “{model}” is a TTS model."))
                    parts.append(audio)
                else:
                    mime = sniff_mime(body, ctype)
                    if not body or not mime:
                        raise TTSApiError(_t(lang, f"{meta['label'] if kind != 'custom' else 'TTS-сервер'} вернул не аудио — проверьте Base URL",
                                             f"{meta['label'] if kind != 'custom' else 'The TTS server'} did not return audio — check the Base URL"))
                    parts.append((body, mime))
                break
    result = merge_audio(parts, lang)
    CACHE.put(cache_key, result)
    return result
