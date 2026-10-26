"""Attachments, speech-to-text and text-to-speech for the Telegram bot.

Everything heavy (PDF/DOCX parsing, local Whisper, Piper/Windows TTS) runs in
worker threads so the bot's event loop — shared with the whole MemuAI Proxy —
never blocks. Speech uses the same MemuAI voice settings as the desktop app
(``voice_stt_provider`` / ``voice_tts_provider``); if a subsystem is not
available in this process the bot says so instead of failing silently.
"""
from __future__ import annotations

import asyncio
import base64
import html as _html
import io
import logging
import re
import threading
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from .telegram_api import BotApi, redact

log = logging.getLogger("memuai.telegram")

TEXT_EXTENSIONS = (
    ".txt", ".md", ".markdown", ".json", ".csv", ".tsv", ".log", ".py", ".js",
    ".ts", ".tsx", ".jsx", ".html", ".htm", ".css", ".xml", ".yml", ".yaml",
    ".ini", ".cfg", ".toml", ".sql", ".sh", ".bat", ".ps1", ".c", ".h", ".cpp",
    ".java", ".kt", ".go", ".rs", ".php", ".rb", ".srt", ".vtt", ".env", ".cs",
    ".swift", ".lua", ".r", ".m", ".scala", ".dart", ".vue", ".svelte", ".tex",
)

MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_ATTACHMENT_CHARS = 30000
MAX_VOICE_SECONDS = 15 * 60

class AttachmentTooLarge(Exception):
    pass

@dataclass
class Attachments:
    notes: List[str] = field(default_factory=list)
    images: List[str] = field(default_factory=list)
    files: List[Dict[str, Any]] = field(default_factory=list)
    transcript: str = ""
    voice: bool = False
    errors: List[str] = field(default_factory=list)
    bigdocs: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(self.notes)

def _tr(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru

def _data_url(mime: str, blob: bytes) -> str:
    return f"data:{mime or 'application/octet-stream'};base64," + base64.b64encode(blob).decode("ascii")

def extract_document_text(name: str, mime: str, blob: bytes) -> str:
    """Text of a document (same formats the desktop extracts); '' when binary."""
    lower = name.lower()
    if lower.endswith(".docx") or mime.endswith("wordprocessingml.document"):
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 20 * 1024 * 1024 or (
                    info.compress_size > 0 and info.file_size / info.compress_size > 100):
                raise ValueError("DOCX is too large when unpacked")
            xml = archive.read("word/document.xml").decode("utf-8", "replace")
        text = re.sub(r"</w:p[^>]*>", "\n", xml)
        return _html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
    if lower.endswith(".pdf") or mime == "application/pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            return ""
        reader = PdfReader(io.BytesIO(blob))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages[:80]).strip()
    if mime.startswith("text/") or lower.endswith(TEXT_EXTENSIONS) or mime in (
            "application/json", "application/xml", "application/x-yaml", "application/javascript"):
        for encoding in ("utf-8-sig", "utf-16", "cp1251", "latin-1"):
            try:
                return blob.decode(encoding).replace("\x00", "").strip()
            except UnicodeDecodeError:
                continue
    return ""

async def _download(api: BotApi, file_id: str, declared: Any, limit: int) -> bytes:
    try:
        size = int(declared or 0)
    except (TypeError, ValueError):
        size = 0
    if size > limit:
        raise AttachmentTooLarge()
    info = await api.call("getFile", file_id=file_id)
    info = info if isinstance(info, dict) else {}
    path = str(info.get("file_path") or "")
    if not path:
        return b""
    if int(info.get("file_size") or 0) > limit:
        raise AttachmentTooLarge()
    try:
        return await api.download(path, max_bytes=limit)
    except OverflowError:
        raise AttachmentTooLarge() from None

async def collect(api: BotApi, message: Dict[str, Any], prefs: Dict[str, Any],
                  lang: str) -> Attachments:
    """Turn Telegram attachments into prompt text, images and MemuAI files."""
    result = Attachments()
    try:
        photos = message.get("photo") or []
        if photos:
            fitting = [p for p in photos if int(p.get("file_size") or 0) <= MAX_IMAGE_BYTES] or photos[:1]
            best = sorted(fitting, key=lambda item: int(item.get("file_size") or 0))[-1]
            blob = await _download(api, str(best.get("file_id") or ""), best.get("file_size"), MAX_IMAGE_BYTES)
            if blob:
                url = _data_url("image/jpeg", blob)
                result.images.append(url)

                result.files.append({"name": "telegram-photo.jpg", "data": url})
                result.notes.append(_tr(lang, "[К сообщению приложено изображение.]",
                                        "[An image is attached to this message.]"))
        document = message.get("document") or {}
        if document:
            await _document(api, document, result, lang)
        voice = message.get("voice") or message.get("video_note") or message.get("audio") or {}
        if voice:
            await _voice(api, voice, result, prefs, lang,
                         spoken=bool(message.get("voice") or message.get("video_note")))
        if message.get("video"):
            result.notes.append(_tr(lang, "[Видео пока не обрабатывается в Telegram — пришлите кадр или опишите его.]",
                                    "[Video is not processed in Telegram yet — send a frame or describe it.]"))
        sticker = message.get("sticker") or {}
        if sticker.get("emoji"):
            result.notes.append(str(sticker["emoji"]))
        location = message.get("location") or {}
        if location.get("latitude") is not None:
            result.notes.append(_tr(lang, "[Геопозиция: ", "[Location: ") +
                                f"{location.get('latitude')}, {location.get('longitude')}]")
        contact = message.get("contact") or {}
        if contact.get("phone_number"):
            name = " ".join(str(contact.get(k) or "") for k in ("first_name", "last_name")).strip()
            result.notes.append(_tr(lang, "[Контакт: ", "[Contact: ") + f"{name} {contact.get('phone_number')}]")
    except AttachmentTooLarge:
        limit_mb = MAX_DOWNLOAD_BYTES // (1024 * 1024)
        text = _tr(lang, f"Вложение больше {limit_mb} МБ (предел Telegram для ботов) — не загружено.",
                   f"The attachment is larger than {limit_mb} MB (Telegram's bot limit) — not loaded.")
        result.notes.append("[" + text + "]")
        result.errors.append(text)
    except Exception as exc:
        log.warning("telegram attachment failed: %s", redact(exc))
        text = _tr(lang, "Не удалось скачать вложение.", "The attachment could not be downloaded.")
        result.notes.append("[" + text + "]")
        result.errors.append(text)
    return result

async def _document(api: BotApi, document: Dict[str, Any], result: Attachments, lang: str) -> None:
    name = str(document.get("file_name") or "file")[:200]
    mime = str(document.get("mime_type") or "")
    is_image = mime.startswith("image/") and mime not in ("image/svg+xml",)
    blob = await _download(api, str(document.get("file_id") or ""), document.get("file_size"),
                           MAX_IMAGE_BYTES if is_image else MAX_DOWNLOAD_BYTES)
    if not blob:
        return
    if is_image:
        url = _data_url(mime, blob)
        result.images.append(url)
        result.files.append({"name": name, "data": url})
        result.notes.append(_tr(lang, f"[Приложено изображение {name}.]", f"[Image {name} is attached.]"))
        return

    taken = None
    try:
        from . import bigdocs_telegram
        taken = await asyncio.to_thread(bigdocs_telegram.take_document, name, blob)
    except Exception as exc:
        log.info("telegram big document not taken: %s", type(exc).__name__)
    if taken and taken.get("doc") and not taken.get("inline"):
        summary = taken["doc"]
        result.bigdocs.append(summary)
        result.files.append({"name": name, "size": len(blob), "note": "big document", "bigdoc": summary})
        pages = f"{'≈' if summary.get('approx_pages') else ''}{summary.get('pages', 0)}"
        result.notes.append(_tr(lang, f"[Файл {name}: {pages} стр. — большой документ, хранится целиком на компьютере.]",
                                f"[File {name}: {pages} pages — a big document, kept whole on the computer.]"))
        return
    try:
        text = taken["inline"] if taken and taken.get("inline") else await asyncio.to_thread(extract_document_text, name, mime, blob)
    except Exception as exc:
        log.info("telegram document not parsed: %s", type(exc).__name__)
        text = ""
    if len(blob) <= 5 * 1024 * 1024:
        result.files.append({"name": name, "data": _data_url(mime or "application/octet-stream", blob)})
    else:
        result.files.append({"name": name, "size": len(blob), "note": "too large to store"})
    if text:
        truncated = len(text) > MAX_ATTACHMENT_CHARS
        body = text[:MAX_ATTACHMENT_CHARS]
        note = _tr(lang, f"[Файл {name}]", f"[File {name}]")
        if truncated:
            note += _tr(lang, f" (показаны первые {MAX_ATTACHMENT_CHARS} символов)",
                        f" (first {MAX_ATTACHMENT_CHARS} characters)")

        result.notes.append(note + "\n<untrusted_file>\n" + body + "\n</untrusted_file>")
    elif name.lower().endswith(".pdf"):
        result.notes.append(_tr(lang, f"[PDF {name} приложен, но текст из него не извлечён (скан или нет pypdf).]",
                                f"[PDF {name} is attached but no text could be extracted (scan or pypdf missing).]"))
    else:
        result.notes.append(_tr(lang, f"[Файл {name} ({mime or '?'}) приложен, но его содержимое не текстовое.]",
                                f"[File {name} ({mime or '?'}) is attached but is not text.]"))

_runtime_lock = threading.Lock()
_runtime: Any = None

def voice_runtime(prefs: Dict[str, Any]) -> Any:
    """One shared VoiceRuntime for this process (the Whisper model is cached)."""
    global _runtime
    from .config import DATA_DIR
    from .voice_runtime import VoiceRuntime
    with _runtime_lock:
        if _runtime is None:
            _runtime = VoiceRuntime(DATA_DIR)
        root = str(prefs.get("voice_models_dir") or "").strip()
        if root:
            try:
                _runtime.set_root(root)
            except Exception:
                pass
        try:
            _runtime.set_custom_models(prefs.get("voice_custom_models") or [])
        except Exception:
            pass
        return _runtime

async def transcribe(audio: bytes, mime: str, prefs: Dict[str, Any], lang: str = "ru") -> str:
    runtime = await asyncio.to_thread(voice_runtime, prefs)
    language = str(prefs.get("voice_language") or "auto")
    if prefs.get("voice_stt_provider") == "api":

        return await runtime.transcribe_api(
            audio, mime, base_url=prefs.get("voice_stt_api_url", ""),
            api_key=prefs.get("voice_stt_api_key", ""),
            model=prefs.get("voice_stt_api_model", "gpt-4o-mini-transcribe"), language=language, lang=lang)
    return await asyncio.to_thread(runtime.transcribe_local, audio, mime,
                                   prefs.get("voice_stt_local_model", "small"), language)

async def _voice(api: BotApi, voice: Dict[str, Any], result: Attachments, prefs: Dict[str, Any],
                 lang: str, *, spoken: bool) -> None:
    result.voice = spoken
    if not prefs.get("telegram_voice_input", True):
        result.notes.append(_tr(lang, "[Голосовое сообщение: расшифровка выключена в настройках бота.]",
                                "[Voice message: transcription is turned off in the bot settings.]"))
        return
    if int(voice.get("duration") or 0) > MAX_VOICE_SECONDS:
        text = _tr(lang, "Голосовое длиннее 15 минут — не расшифровано.",
                   "The voice message is longer than 15 minutes — not transcribed.")
        result.notes.append("[" + text + "]")
        result.errors.append(text)
        return
    blob = await _download(api, str(voice.get("file_id") or ""), voice.get("file_size"), MAX_DOWNLOAD_BYTES)
    if not blob:
        return
    mime = str(voice.get("mime_type") or ("audio/ogg" if voice.get("duration") is not None else "audio/mpeg"))
    if "video" in mime or voice.get("length"):
        mime = "audio/mp4"
    try:
        text = (await transcribe(blob, mime, prefs, lang)).strip()
    except FileNotFoundError:
        text = ""
        result.errors.append(_tr(lang, "Модель распознавания речи не скачана: MemuAI → Настройки → Голос и Live.",
                                 "The speech model is not downloaded: MemuAI → Settings → Voice & Live."))
    except Exception as exc:
        log.info("telegram transcription failed: %s", redact(exc))
        text = ""
        result.errors.append(_tr(lang, "Не удалось расшифровать голосовое: ", "Could not transcribe the voice message: ")
                             + redact(str(exc))[:160])
    if text:
        result.transcript = text
        result.notes.append(text)
    else:
        result.notes.append(_tr(lang, "[Голосовое сообщение не расшифровано.]",
                                "[The voice message could not be transcribed.]"))

def tts_available(prefs: Dict[str, Any]) -> bool:
    provider = str(prefs.get("voice_tts_provider") or "windows")
    if provider in ("api", "local", "downloaded"):
        return True
    import os
    return os.name == "nt"

async def synthesize(text: str, prefs: Dict[str, Any]) -> Tuple[bytes, str]:
    """Speech for ``text`` with the user's MemuAI TTS settings → (audio, mime)."""
    from .voice_runtime import prepare_tts_text
    from .tts_providers import speed_from_rate
    from .telegram_format import html_to_plain, markdown_to_telegram_html
    plain = html_to_plain(markdown_to_telegram_html(text))
    plain = re.sub(r"[│─┼▫️•◦▪]", " ", plain)
    spoken = prepare_tts_text(plain, limit=4000)
    runtime = await asyncio.to_thread(voice_runtime, prefs)
    provider = str(prefs.get("voice_tts_provider") or "windows")
    if provider in ("api", "local"):
        local = provider == "local"
        return await runtime.speak_api(
            spoken, base_url=prefs.get("voice_tts_local_url" if local else "voice_tts_api_url", ""),
            api_key=prefs.get("voice_tts_local_api_key" if local else "voice_tts_api_key", ""),
            model=prefs.get("voice_tts_local_model" if local else "voice_tts_api_model", "tts-1"),
            voice=prefs.get("voice_tts_local_voice" if local else "voice_tts_api_voice", "alloy"),
            speed=speed_from_rate(prefs.get("voice_tts_rate", 0)), lang=str(prefs.get("language") or "ru"))
    if provider == "downloaded":
        detected = runtime.detect_language(spoken)
        selected = str(prefs.get("voice_tts_local_model") or "")
        catalog = await asyncio.to_thread(runtime.tts_catalog, selected)
        model = next((row for row in catalog if row.get("downloaded") and row.get("active")
                      and runtime._supports(row, detected)), None) or next(
            (row for row in catalog if row.get("downloaded") and runtime._supports(row, detected)), None)
        if not model:
            raise RuntimeError("no downloaded TTS voice for " + detected)
        return await asyncio.to_thread(runtime.speak_local_tts, spoken, model["id"], detected)
    voice_name = str(prefs.get("voice_tts_voice") or "")
    return await asyncio.to_thread(runtime.speak_windows, spoken, voice_name,
                                   int(prefs.get("voice_tts_rate", 0) or 0),
                                   int(prefs.get("voice_tts_volume", 100) or 100))

async def send_speech(api: BotApi, chat_id: str, audio: bytes, mime: str, *,
                      reply_to: int = 0, caption: str = "") -> None:
    """MP3/OGG/M4A become a voice bubble; anything else (WAV) a playable audio."""
    payload: Dict[str, Any] = {"chat_id": chat_id}
    if caption:
        payload["caption"] = caption[:1000]
    if reply_to:
        payload["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
    if mime in ("audio/mpeg", "audio/mp3", "audio/ogg", "audio/opus", "audio/mp4", "audio/m4a"):
        extension = {"audio/ogg": "ogg", "audio/opus": "ogg", "audio/mp4": "m4a", "audio/m4a": "m4a"}.get(mime, "mp3")
        await api.call("sendVoice", essential=True, files={"voice": (f"memuai.{extension}", audio, mime)}, **payload)
    else:
        payload["title"] = "MemuAI"
        await api.call("sendAudio", essential=True, files={"audio": ("memuai.wav", audio, mime or "audio/wav")},
                       **payload)
