"""Speech-to-text for Telegram voice messages and the Mini App (Round 29).

One place decides whether audio can be transcribed *on this computer* and does it:

* **Engine.** MemuAI's local Whisper (faster-whisper; models are downloaded on demand in
  MemuAI → Settings → Voice). The Proxy process (where the bot and the Mini App live) runs it
  in-process when faster-whisper is importable there (source runs); the built Proxy ships without the
  speech runtime, so the audio then goes to the running MemuAI window (``/api/voice/transcribe``, the
  same route MemuCode's dictation uses — local, 127.0.0.1 only). A user who chose an OpenAI-compatible
  speech API in Settings → Voice keeps it: that is their explicit choice.
* **Availability.** ``status(prefs)`` — without a downloaded local model (or with the voice input switched
  off) the feature is unavailable and the bot / Mini App say exactly how to enable it.
* **Formats.** Telegram voice notes are OGG/Opus, video notes MP4, audio files MP3/M4A/OGG/WAV/FLAC; the
  Mini App records WebM/Opus (Chromium) or MP4/AAC (Safari). faster-whisper decodes all of them with PyAV
  (libav bundled in the ``av`` wheel) — no ffmpeg binary is involved.
* **Review.** ``telegram_voice_review`` (default off): the transcript is shown first and sent to the model
  only after «Send» (bot: inline buttons; Mini App: the text lands in the input box).
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import logging
import re
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger("memuai.telegram")

MAX_SECONDS = 15 * 60
MAX_BYTES = 20 * 1024 * 1024            # the Bot API download limit; the Mini App upload limit too
MIN_BYTES = 600
REVIEW_TTL = 30 * 60
APP_TIMEOUT = 300.0

AUDIO_EXT = {".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg", ".mp3": "audio/mpeg",
             ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav",
             ".flac": "audio/flac", ".webm": "audio/webm", ".weba": "audio/webm", ".amr": "audio/amr"}
MIME_ALIASES = {"audio/x-m4a": "audio/mp4", "audio/m4a": "audio/mp4", "video/mp4": "audio/mp4",
                "audio/opus": "audio/ogg", "application/ogg": "audio/ogg", "audio/x-wav": "audio/wav",
                "audio/wave": "audio/wav", "audio/vnd.wave": "audio/wav", "audio/mp3": "audio/mpeg",
                "audio/x-flac": "audio/flac", "video/webm": "audio/webm", "audio/x-aac": "audio/aac"}

_semaphore: Optional[asyncio.Semaphore] = None
_pending: Dict[str, Dict[str, Any]] = {}


class VoiceUnavailable(RuntimeError):
    """Transcription cannot run here; ``str(exc)`` is the user-facing hint."""

    def __init__(self, message: str, code: str = "unavailable") -> None:
        super().__init__(message)
        self.code = code


def _tr(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru


# --------------------------------------------------------------------------- formats

def normalize_mime(mime: Any, name: Any = "") -> str:
    """A plain audio MIME type (codec parameters dropped) the speech engine understands."""
    value = str(mime or "").split(";", 1)[0].strip().lower()
    value = MIME_ALIASES.get(value, value)
    if not value.startswith("audio/"):
        suffix = Path(str(name or "")).suffix.lower()
        value = AUDIO_EXT.get(suffix, "audio/ogg" if not value else value)
    return value if value.startswith("audio/") else "audio/ogg"


def is_audio_message(message: Dict[str, Any]) -> bool:
    if any(message.get(key) for key in ("voice", "video_note", "audio")):
        return True
    document = message.get("document") or {}
    return bool(document) and (str(document.get("mime_type") or "").startswith("audio/")
                               or Path(str(document.get("file_name") or "")).suffix.lower() in AUDIO_EXT)


def _audio_of(message: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """The file object of an audio message and whether the user *spoke* it (voice / video note)."""
    for key, spoken in (("voice", True), ("video_note", True), ("audio", False), ("document", False)):
        if message.get(key):
            return dict(message[key]), spoken
    return {}, False


# --------------------------------------------------------------------------- availability

def _runtime(prefs: Dict[str, Any]) -> Any:
    from .telegram_media import voice_runtime
    return voice_runtime(prefs)


def _window_url() -> str:
    """The running MemuAI window (it has the local speech engine); '' when it is not running."""
    try:
        from .config import DATA_DIR
        data = json.loads((Path(DATA_DIR) / "memuai" / "memuai.json").read_text(encoding="utf-8"))
        url = str(data.get("url") or "").rstrip("/")
    except Exception:
        return ""
    return url if url.startswith(("http://127.0.0.1", "http://localhost")) else ""


def engine_in_process() -> bool:
    try:
        return importlib.util.find_spec("faster_whisper") is not None
    except (ImportError, ValueError):
        return False


def status(prefs: Dict[str, Any], *, lang: Optional[str] = None) -> Dict[str, Any]:
    """Whether audio can be transcribed now, by what, and a hint when it cannot."""
    lang = lang or ("en" if prefs.get("language") == "en" else "ru")
    out: Dict[str, Any] = {"available": False, "reason": "", "engine": "", "model": "",
                           "review": bool(prefs.get("telegram_voice_review")), "max_seconds": MAX_SECONDS,
                           "max_bytes": MAX_BYTES, "message": ""}
    provider = str(prefs.get("voice_stt_provider") or "local")
    if provider == "api":
        from .stt_providers import provider_for_url
        url = str(prefs.get("voice_stt_api_url") or "").strip()
        if url and provider_for_url(url) != "custom" and not str(prefs.get("voice_stt_api_key") or "").strip():
            out.update(reason="no_api_key", message=_tr(lang,
                "Укажите API-ключ сервиса распознавания: MemuAI → Настройки → Голос и Live → Распознавание речи.",
                "Enter the recognition service API key: MemuAI → Settings → Voice & Live → Speech recognition."))
            return out
        if url:
            out.update(available=True, engine="api", model=str(prefs.get("voice_stt_api_model") or ""))
            return out
        out.update(reason="no_api", message=_tr(
            lang, "Распознавание речи настроено на API, но адрес API не указан: MemuAI → Настройки → Голос.",
            "Speech recognition is set to an API but no API address is set: MemuAI → Settings → Voice."))
        return out
    model = str(prefs.get("voice_stt_local_model") or "small")
    out["model"] = model
    try:
        downloaded = bool(_runtime(prefs)._is_downloaded(model))
    except Exception:
        downloaded = False
    if not downloaded:
        out.update(reason="no_model", message=_tr(
            lang, "Локальная модель распознавания речи не установлена. Скачайте её на компьютере: MemuAI → "
                  "Настройки → Голос → «Распознавание речи» → модель Whisper (например, Small) → «Скачать».",
            "No local speech recognition model is installed. Download one on the computer: MemuAI → Settings → "
            "Voice → “Speech recognition” → a Whisper model (e.g. Small) → “Download”."))
        return out
    if engine_in_process():
        out.update(available=True, engine="local")
        return out
    if _window_url():
        out.update(available=True, engine="app")
        return out
    out.update(reason="no_engine", message=_tr(
        lang, "Локальное распознавание речи работает внутри окна MemuAI — запустите MemuAI на компьютере.",
        "Local speech recognition runs inside the MemuAI window — start MemuAI on the computer."))
    return out


# --------------------------------------------------------------------------- transcription

TYPING_EVERY = 4.5      # seconds; Telegram's «typing…» fades after ~5 s

_ERRORS_EN = (
    ("Речь не распознана", "No speech was recognised"),
    ("Сначала скачайте выбранную модель речи", "Download the selected speech model first"),
    ("Локальный движок речи не установлен", "The local speech engine is not installed"),
    ("Неизвестная локальная модель речи", "Unknown local speech model"),
    ("Не удалось прочитать аудио", "Could not read the audio"),
)


def _friendly(error: Any, lang: str) -> str:
    text = str(error or "").strip() or type(error).__name__
    try:
        from .telegram_api import redact
        text = redact(text)
    except Exception:
        pass
    if lang == "en":
        for ru, en in _ERRORS_EN:
            if ru in text:
                return en
    return text[:300]


def _post_window(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    request = urllib.request.Request(url + "/api/voice/transcribe", data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))     # 127.0.0.1 only, never a proxy
    try:
        with opener.open(request, timeout=APP_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:          # the window answers errors as JSON with a 4xx/5xx code
        try:
            data = json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:
            data = {"error": f"HTTP {exc.code}"}
    return data if isinstance(data, dict) else {}


async def transcribe(audio: bytes, mime: str, prefs: Dict[str, Any], *, lang: str = "ru",
                     name: str = "", telegram_use: bool = False) -> str:
    """Text of ``audio``; raises ``VoiceUnavailable`` (hint) or ``RuntimeError`` (friendly message)."""
    global _semaphore
    info = status(prefs, lang=lang)
    if not info["available"]:
        raise VoiceUnavailable(info["message"], info["reason"])
    if len(audio) > MAX_BYTES:
        raise RuntimeError(_tr(lang, "Аудио больше 20 МБ — не расшифровано.", "The audio is larger than 20 MB — not transcribed."))
    if len(audio) < MIN_BYTES:
        raise RuntimeError(_tr(lang, "Запись слишком короткая.", "The recording is too short."))
    mime = normalize_mime(mime, name)
    language = str(prefs.get("voice_language") or "auto")
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(1)          # one Whisper run at a time: it uses every CPU core
    async with _semaphore:
        try:
            if info["engine"] == "api":
                runtime = await asyncio.to_thread(_runtime, prefs)
                text = await runtime.transcribe_api(
                    audio, mime, base_url=prefs.get("voice_stt_api_url", ""),
                    api_key=prefs.get("voice_stt_api_key", ""),
                    model=prefs.get("voice_stt_api_model", "gpt-4o-mini-transcribe"), language=language, lang=lang)
            elif info["engine"] == "local":
                runtime = await asyncio.to_thread(_runtime, prefs)
                text = await asyncio.to_thread(runtime.transcribe_local, audio, mime, info["model"], language)
            else:
                data_url = f"data:{mime};base64," + base64.b64encode(audio).decode("ascii")
                payload = {"audio": data_url}
                if telegram_use:
                    payload.update(telegram_use=True, raw=True)
                result = await asyncio.to_thread(_post_window, _window_url(), payload)
                if result.get("error") and not result.get("text"):
                    raise RuntimeError(str(result.get("error")))
                text = str(result.get("text") or "")
        except VoiceUnavailable:
            raise
        except (OSError, urllib.error.URLError) as exc:
            if info["engine"] == "app":
                raise VoiceUnavailable(_tr(lang, "Окно MemuAI не отвечает — откройте MemuAI на компьютере и повторите.",
                                           "The MemuAI window does not answer — open MemuAI on the computer and retry."),
                                       "no_engine") from None
            raise RuntimeError(_friendly(exc, lang)) from None
        except Exception as exc:
            log.info("voice transcription failed: %s", _friendly(exc, "en"))
            raise RuntimeError(_friendly(exc, lang)) from None
    text = " ".join(str(text or "").split()) if "\n" not in str(text or "") else str(text).strip()
    if not text.strip():
        raise RuntimeError(_tr(lang, "Речь не распознана.", "No speech was recognised."))
    return text.strip()


def decode_upload(value: Any) -> Tuple[bytes, str]:
    """``data:audio/...;base64,...`` from the Mini App → (bytes, mime)."""
    raw = str(value or "")
    match = re.match(r"^data:([\w.+/-]+(?:;[\w.=+-]+)*)?;base64,(.*)$", raw, re.S)
    if not match:
        raise ValueError("bad audio")
    mime = (match.group(1) or "audio/webm").split(";", 1)[0]
    if not (mime.startswith("audio/") or mime in ("video/webm", "video/mp4", "application/ogg")):
        raise ValueError("not audio")
    try:
        blob = base64.b64decode(re.sub(r"\s+", "", match.group(2)), validate=True)
    except ValueError:
        raise ValueError("bad audio") from None
    return blob, mime


# --------------------------------------------------------------------------- the bot

def _escape(text: str) -> str:
    import html
    return html.escape(text)


def _keyboard(token: str, lang: str) -> Dict[str, Any]:
    return {"inline_keyboard": [[{"text": _tr(lang, "▶️ Отправить", "▶️ Send"), "callback_data": "vtr:send:" + token},
                                 {"text": _tr(lang, "✖ Отмена", "✖ Cancel"), "callback_data": "vtr:drop:" + token}]]}


def _prune() -> None:
    now = time.time()
    for key in [key for key, row in _pending.items() if now - row["ts"] > REVIEW_TTL]:
        _pending.pop(key, None)


async def route_message(api: Any, chat_id: str, message: Dict[str, Any], prefs: Dict[str, Any],
                        lang: str) -> Optional[Dict[str, Any]]:
    """A voice/audio message from the bot: transcribe it locally.

    Returns ``{"text", "spoke"}`` to send to the model now, or ``None`` when the message is handled here
    (feature unavailable — the hint was sent; transcription failed; review mode — the transcript waits for
    «Send»)."""
    reply_to = int(message.get("message_id") or 0)

    async def say(text: str, markup: Optional[Dict[str, Any]] = None, *, silent: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                                   "disable_notification": silent}
        if reply_to:
            payload["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        if markup:
            payload["reply_markup"] = markup
        try:
            result = await api.call("sendMessage", **payload)
            return result if isinstance(result, dict) else {}
        except Exception as exc:
            log.info("telegram voice: could not send: %s", _friendly(exc, "en"))
            return {}

    item, spoke = _audio_of(message)
    caption = str(message.get("caption") or "").strip()
    info = status(prefs, lang=lang)
    if not info["available"]:
        await say("🎙 " + _escape(info["message"]), silent=False)
        return None
    seconds = int(item.get("duration") or 0)
    if seconds > MAX_SECONDS:
        await say("⚠️ " + _escape(_tr(lang, "Запись длиннее 15 минут — не расшифрована.",
                                     "The recording is longer than 15 minutes — not transcribed.")), silent=False)
        return None
    if int(item.get("file_size") or 0) > MAX_BYTES:
        await say("⚠️ " + _escape(_tr(lang, "Файл больше 20 МБ — Telegram не даёт ботам скачивать такие.",
                                     "The file is larger than 20 MB — Telegram does not let bots download it.")),
                  silent=False)
        return None
    async def keep_typing() -> None:
        # Whisper takes seconds to minutes on a laptop CPU; Telegram's «typing…» fades after ~5 s, so it is
        # renewed until the transcript is ready (otherwise the chat looks dead while the computer works).
        while True:
            try:
                await api.call("sendChatAction", chat_id=chat_id, action="typing")
            except Exception:
                pass
            await asyncio.sleep(TYPING_EVERY)

    typing = asyncio.create_task(keep_typing())
    try:
        return await _transcribe_and_route(api, chat_id, message, prefs, lang, item, spoke, caption, reply_to, say)
    finally:
        typing.cancel()


async def _transcribe_and_route(api: Any, chat_id: str, message: Dict[str, Any], prefs: Dict[str, Any], lang: str,
                                item: Dict[str, Any], spoke: bool, caption: str, reply_to: int,
                                say: Any) -> Optional[Dict[str, Any]]:
    try:
        from .telegram_media import _download
        blob = await _download(api, str(item.get("file_id") or ""), item.get("file_size"), MAX_BYTES)
    except Exception as exc:
        too_large = type(exc).__name__ == "AttachmentTooLarge"
        await say("⚠️ " + _escape(_tr(lang, "Файл больше 20 МБ — не расшифрован." if too_large else "Не удалось скачать запись.",
                                     "The file is larger than 20 MB — not transcribed." if too_large else "Could not download the recording.")),
                  silent=False)
        return None
    mime = str(item.get("mime_type") or ("audio/ogg" if message.get("voice") else ""))
    if message.get("video_note"):
        mime = "audio/mp4"
    try:
        text = await transcribe(blob, mime, prefs, lang=lang, name=str(item.get("file_name") or ""))
    except VoiceUnavailable as exc:
        await say("🎙 " + _escape(str(exc)), silent=False)
        return None
    except Exception as exc:
        await say("⚠️ " + _escape(_tr(lang, "Не удалось расшифровать: ", "Could not transcribe: ") + str(exc)), silent=False)
        return None
    full = f"{caption}\n\n{text}".strip() if caption else text
    if prefs.get("telegram_voice_review"):
        _prune()
        token = secrets.token_hex(5)
        sent = await say("🎙 <i>" + _escape(text[:3500]) + "</i>\n\n" + _escape(_tr(
            lang, "Отправить модели? Или напишите исправленный текст сообщением.",
            "Send it to the model? Or type the corrected text as a message.")), _keyboard(token, lang), silent=False)
        _pending[token] = {"chat_id": str(chat_id), "text": full, "spoke": spoke, "ts": time.time(),
                           "message_id": int(sent.get("message_id") or 0), "reply_to": reply_to}
        return None
    await say("🎙 <i>" + _escape(text[:3500]) + "</i>")
    return {"text": full, "spoke": spoke}


async def handle_callback(api: Any, chat_id: str, message_id: int, data: str, lang: str) -> Optional[Dict[str, Any]]:
    """``vtr:send:<token>`` / ``vtr:drop:<token>`` → the prompt to send now, or None."""
    parts = str(data or "").split(":")
    if len(parts) != 3 or parts[0] != "vtr":
        return None
    _prune()
    row = _pending.get(parts[2])
    if row is not None and row["chat_id"] != str(chat_id):
        row = None
    if row is not None:
        _pending.pop(parts[2], None)
    note = ""
    if row is None:
        note = _tr(lang, "\n\n⌛ Запись устарела — отправьте её ещё раз.", "\n\n⌛ This recording has expired — send it again.")
    elif parts[1] == "drop":
        note = _tr(lang, "\n\n✖ Не отправлено.", "\n\n✖ Not sent.")
    try:
        if note and row is not None:
            await api.call("editMessageText", chat_id=chat_id, message_id=message_id, parse_mode="HTML",
                           text="🎙 <i>" + _escape(row["text"][:3500]) + "</i>" + _escape(note),
                           reply_markup={"inline_keyboard": []})
        else:
            await api.call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
                           reply_markup={"inline_keyboard": []})
    except Exception:
        pass
    if row is None or parts[1] != "send":
        return None
    return {"text": row["text"], "spoke": row["spoke"], "reply_to": row.get("reply_to") or 0}


def drop_pending_for(chat_id: str) -> None:
    """A typed message replaces a transcript waiting for review in that chat."""
    for key in [key for key, row in _pending.items() if row["chat_id"] == str(chat_id)]:
        _pending.pop(key, None)


