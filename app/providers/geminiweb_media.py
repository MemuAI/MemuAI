"""Gemini Web: вложения на вход и сгенерированные картинки/видео на выход (раунд 41b).

Вход. Из последнего сообщения пользователя берутся части OpenAI ``image_url`` / ``input_image`` (data: или http(s)),
``file`` / ``input_file`` (``file_data`` data:/base64 + ``filename``, ``file_url``), ``video_url`` и сырые блоки
Anthropic ``image`` (обычно anthropic_api уже превратил их в image_url). Каждое вложение загружается в
content-push.googleapis.com/upload, а в f.req уходит ``inner[0][3] = [[[ссылка], имя], …]`` — как у веб-клиента.

Выход. Картинки, которые нарисовал Gemini (Imagen / «Nano Banana»), и видео Veo лежат по адресам
googleusercontent.com, которые без кук Google не открываются. Proxy скачивает их куками аккаунта (картинки — в
полном размере ``=s2048-rj``; видео, пока оно рендерится, отвечает 206 — опрашиваем раз в 10 с), кладёт в папку
данных ``geminiweb_media/<sha256>.<ext>`` и отдаёт по ``GET /v1/geminiweb/media/<файл>`` (имя — хэш содержимого,
угадать нельзя; маршрут без пароля, как подписанная ссылка). В ответ модели это попадает markdown-картинкой /
ссылкой на этот адрес; чат MemuAI сохраняет картинку в файлы профиля (media_cache читает файл напрямую).

Факты о полях ответа (rich content c[12]: поле 7 — сгенерированные картинки, 59 — видео, 1 — картинки из сети)
и о суффиксе =s2048-rj — HanaokaYuzu/Gemini-API master (AGPL, код не переносился), октябрь 2026.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import mimetypes
import os
import re
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import DATA_DIR

MEDIA_DIR = Path(DATA_DIR) / "geminiweb_media"
MEDIA_ROUTE = "/v1/geminiweb/media/"
MEDIA_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(png|jpg|webp|gif|mp4|webm)$")
MEDIA_URL_RE = re.compile(r"/v1/geminiweb/media/([0-9a-f]{64}\.(?:png|jpg|webp|gif|mp4|webm))")
MAX_FILES = 10
MAX_INPUT_BYTES = 100 * 1024 * 1024       # веб-клиент Gemini принимает файлы до ~100 МБ
MAX_OUTPUT_BYTES = 200 * 1024 * 1024
KEEP_DAYS = 30
VIDEO_WAIT = 240.0                        # сколько ждём, пока Veo дорендерит видео (206 → 200)
VIDEO_POLL = 10.0
_DATA_URL_RE = re.compile(r"^data:([\w/+.-]+)?(?:;[\w=.-]+)*;base64,(.*)$", re.S | re.I)

MIME_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif",
            "video/mp4": "mp4", "video/webm": "webm"}


@dataclass
class Attachment:
    data: bytes
    mime: str
    name: str


@dataclass
class GeneratedMedia:
    kind: str                 # image | video
    url: str                  # адрес googleusercontent.com (картинка — превью, видео — mp4)
    thumb: str = ""           # превью видео
    alt: str = ""


class AttachmentError(ValueError):
    """Вложение не удалось прочитать (битый base64, недоступный адрес, слишком большой файл)."""


# ------------------------------------------------------------------ вход


def pending_parts(messages: List[Dict[str, Any]]) -> List[Tuple[str, str, str]]:
    """Части последнего сообщения пользователя → [(источник data:/http(s)/b64:mime, mime-подсказка, имя)]."""
    last = next((m for m in reversed(messages or []) if m.get("role") == "user"), None)
    if not last or not isinstance(last.get("content"), list):
        return []
    out: List[Tuple[str, str, str]] = []
    for part in last["content"]:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        src, mime, name = "", "", ""
        if kind in ("image_url", "input_image"):
            value = part.get("image_url")
            src = value.get("url", "") if isinstance(value, dict) else str(value or "")
            mime = "image/*"
        elif kind == "video_url":
            value = part.get("video_url")
            src = value.get("url", "") if isinstance(value, dict) else str(value or "")
            mime = "video/*"
        elif kind in ("file", "input_file"):
            value = part.get("file") if isinstance(part.get("file"), dict) else part
            name = str(value.get("filename") or value.get("name") or "")
            data = value.get("file_data") or ""
            if data:
                data = str(data)
                src = data if data.startswith("data:") else f"b64:{mimetypes.guess_type(name)[0] or ''}:{data}"
            else:
                url = value.get("file_url") or value.get("url") or ""
                src = url.get("url", "") if isinstance(url, dict) else str(url or "")
        elif kind == "file_url":
            value = part.get("file_url")
            src = value.get("url", "") if isinstance(value, dict) else str(value or "")
        elif kind == "image":            # сырой блок Anthropic
            source = part.get("source") or {}
            if source.get("type") == "base64":
                src = f"data:{source.get('media_type') or 'image/png'};base64,{source.get('data') or ''}"
            elif source.get("type") == "url":
                src = str(source.get("url") or "")
            mime = "image/*"
        if src:
            out.append((src.strip(), mime, name))
    return out[:MAX_FILES]


def sniff_mime(data: bytes, fallback: str = "application/octet-stream") -> str:
    head = data[:16]
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[4:8] == b"ftyp":
        return "video/quicktime" if data[8:10] == b"qt" else "video/mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    if head.startswith(b"%PDF"):
        return "application/pdf"
    return fallback


def decode_source(src: str) -> Optional[Tuple[bytes, str]]:
    """data:/b64: → (байты, mime); http(s) → None (качать отдельно)."""
    match = _DATA_URL_RE.match(src)
    declared = ""
    payload = None
    if match:
        declared, payload = (match.group(1) or ""), match.group(2)
    elif src.startswith("b64:"):
        _, declared, payload = src.split(":", 2)
    if payload is None:
        return None
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", payload), validate=False)
    except (binascii.Error, ValueError) as exc:
        raise AttachmentError("Вложение: повреждённые данные base64") from exc
    if not raw:
        raise AttachmentError("Вложение пустое")
    if len(raw) > MAX_INPUT_BYTES:
        raise AttachmentError("Вложение больше 100 МБ — Gemini такие не принимает")
    return raw, declared or sniff_mime(raw)


def file_name(index: int, mime: str, given: str = "") -> str:
    if given:
        return os.path.basename(given)[:120] or given
    ext = mimetypes.guess_extension(mime or "") or ""
    ext = {".jpe": ".jpg", ".jpeg": ".jpg"}.get(ext, ext)
    kind = (mime or "file").split("/")[0] if (mime or "").startswith(("image/", "video/", "audio/")) else "file"
    return f"{kind}{index}{ext}"


async def load_attachments(messages: List[Dict[str, Any]], fetch_remote) -> List[Attachment]:
    """Все вложения последнего сообщения; http(s) качает ``fetch_remote(url) -> bytes`` (с защитой от SSRF)."""
    out: List[Attachment] = []
    for i, (src, hint, name) in enumerate(pending_parts(messages), start=1):
        decoded = decode_source(src)
        if decoded is None:
            if not re.match(r"^https?://", src, re.I):
                continue
            try:
                raw = await fetch_remote(src)
            except Exception as exc:  # noqa: BLE001 — понятная ошибка вместо трассировки
                raise AttachmentError(f"Не удалось скачать вложение {src[:80]}: {exc}") from exc
            if len(raw) > MAX_INPUT_BYTES:
                raise AttachmentError("Вложение больше 100 МБ — Gemini такие не принимает")
            guessed = mimetypes.guess_type(urllib.parse.urlparse(src).path)[0] or ""
            decoded = (raw, sniff_mime(raw, guessed or "application/octet-stream"))
            name = name or os.path.basename(urllib.parse.urlparse(src).path)
        raw, mime = decoded
        if mime in ("", "application/octet-stream") or mime.endswith("/*"):
            mime = sniff_mime(raw, mimetypes.guess_type(name)[0] or (hint if not hint.endswith("/*") else "")
                              or "application/octet-stream")
        out.append(Attachment(raw, mime, file_name(i, mime, name)))
    return out


async def fetch_public(url: str) -> bytes:
    """http(s)-вложение по ссылке: только публичные адреса (та же защита от SSRF, что у кэша картинок чата)."""
    from .. import media_cache
    return await asyncio.wait_for(media_cache._download_public(url), media_cache.TIMEOUT)


# ------------------------------------------------------------------ выход


def full_size(url: str) -> str:
    """Превью сгенерированной картинки → полный размер (как кнопка «Скачать» в Gemini)."""
    if "=s1024-rj" in url:
        return url.replace("=s1024-rj", "=s2048-rj")
    if re.search(r"=s\d+(-[\w-]+)?$", url) or "=s2048-rj" in url:
        return url
    return url + "=s2048-rj"


def _prune(now: float) -> None:
    try:
        for path in MEDIA_DIR.iterdir():
            if path.is_file() and now - path.stat().st_mtime > KEEP_DAYS * 86400:
                path.unlink(missing_ok=True)
    except OSError:
        pass


def store(data: bytes, mime: str) -> str:
    """Байты → файл geminiweb_media/<sha256>.<ext>; возвращает имя файла."""
    ext = MIME_EXT.get(mime) or MIME_EXT.get(sniff_mime(data)) or ("mp4" if mime.startswith("video/") else "png")
    name = f"{hashlib.sha256(data).hexdigest()}.{ext}"
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    path = MEDIA_DIR / name
    if not path.exists():
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        _prune(time.time())
    return name


def local_path(name_or_url: str) -> Optional[Path]:
    """Имя файла или адрес …/v1/geminiweb/media/<файл> → путь в папке данных (если файл есть)."""
    match = MEDIA_URL_RE.search(name_or_url or "")
    name = match.group(1) if match else (name_or_url or "")
    if not MEDIA_NAME_RE.match(name):
        return None
    path = MEDIA_DIR / name
    return path if path.is_file() else None


def public_base(settings: Optional[Dict[str, Any]] = None) -> str:
    """Адрес Proxy для ссылок на медиа: настройка провайдера media_base_url или http://127.0.0.1:<порт>."""
    from ..config import config_store
    custom = str((settings or {}).get("media_base_url") or "").strip().rstrip("/")
    if re.match(r"^https?://", custom, re.I):
        return custom
    host = str(getattr(config_store.settings, "host", "") or "127.0.0.1")
    if host in ("0.0.0.0", "::", "", "localhost"):
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{int(getattr(config_store.settings, 'port', 8317) or 8317)}"


def media_url(name: str, settings: Optional[Dict[str, Any]] = None) -> str:
    return f"{public_base(settings)}{MEDIA_ROUTE}{name}"


def markdown(items: List[Tuple[GeneratedMedia, str, str]]) -> str:
    """[(медиа, адрес для клиента, адрес превью)] → markdown: картинки ![…](…), видео — превью + ссылка."""
    lines: List[str] = []
    n_img = n_vid = 0
    for media, url, thumb in items:
        if media.kind == "image":
            n_img += 1
            lines.append(f"![Gemini image {n_img}]({url})")
        else:
            n_vid += 1
            if thumb:
                lines.append(f"![Gemini video {n_vid}]({thumb})")
            lines.append(f"[▶ Gemini video {n_vid} (mp4)]({url})")
    return "\n\n".join(lines)


def urls_in(text: str) -> List[str]:
    """Адреса медиа Proxy из текста ответа (для /v1/images/generations)."""
    return list(dict.fromkeys(m.group(0) for m in re.finditer(
        r"https?://[^\s)\]]+/v1/geminiweb/media/[0-9a-f]{64}\.(?:png|jpg|webp|gif|mp4|webm)", text or "")))
