"""Round 29 (r29c-media): image and video generation in the Telegram bot and the Mini App.

Runs inside MemuAI Proxy (the bot and the Mini App live there). It reuses the desktop pieces instead of copying
them:

* the model lists come from ``admin_api.pg_state`` and the generation from ``admin_api.pg_image`` / ``pg_video``
  (called in-process with a loopback request — the same Genspark / Custom / Gemini / Veo paths as the chat window);
* the routing rules (heuristic, decision prompt, generation prompt, friendly errors, video file store) come from
  ``app/media_routing.py``;
* preferences are the desktop ones: ``media_image_model`` / ``media_video_model`` ("" automatic, "off" only by
  command, an id), ``decision_model`` ("" — the main model decides through hidden blocks) and ``media_autoprompt``.

Bot: ``/image …`` and ``/video …`` generate right away; with a Decisions model an ordinary message that asks for a
picture is routed before the answer; without it the main model writes a hidden ``memuai-image`` /
``memuai-video`` block (``prompt_block``) and the result follows its answer. Results go out as ``sendPhoto`` /
``sendVideo`` (``sendDocument`` when too big) and are saved into the MemuAI chat (``images`` / ``videos`` +
``media`` like the desktop app), so they also show in the chat window.
"""
from __future__ import annotations

import asyncio
import base64
import html
import json
import logging
import re
import secrets
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("memuai.telegram")

PHOTO_LIMIT = 10 * 1024 * 1024        # Bot API: sendPhoto upload
UPLOAD_LIMIT = 50 * 1024 * 1024       # Bot API: sendVideo / sendDocument upload
DOWNLOAD_LIMIT = 160 * 1024 * 1024
MODELS_TTL = 20.0
GENERATION_TIMEOUT = 25 * 60.0
COMMANDS = {"/image": "image", "/img": "image", "/picture": "image", "/video": "video"}

_models_cache: Dict[str, Any] = {"at": 0.0, "value": None}


def _tr(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru


# --------------------------------------------------------------------------- in-process Proxy calls


def _loopback_request(path: str, body: Optional[Dict[str, Any]] = None, method: str = "POST"):
    """A Starlette request that passes ``admin_api._auth`` (trusted Host + a short-lived panel session)."""
    from starlette.requests import Request
    from . import admin_api
    from .config import config_store
    host = config_store.effective_host.strip().strip("[]").lower()
    if host in ("0.0.0.0", "::", "", "localhost") or host in getattr(admin_api, "_LOOPBACK_HOSTS", ()):
        host = "127.0.0.1"
    token = secrets.token_urlsafe(24)
    admin_api._sessions[token] = time.time() + GENERATION_TIMEOUT + 120
    raw = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
    scope = {"type": "http", "method": method, "path": "/api/admin" + path, "raw_path": ("/api/admin" + path).encode(),
             "query_string": b"", "scheme": "http", "server": (host, 80), "client": ("127.0.0.1", 0),
             "http_version": "1.1", "root_path": "",
             "headers": [(b"host", host.encode("idna") if host.isascii() else b"127.0.0.1"),
                         (b"authorization", ("Bearer " + token).encode()),
                         (b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]}
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(scope, receive), token


def _drop_token(token: str) -> None:
    try:
        from . import admin_api
        admin_api._sessions.pop(token, None)
    except Exception:
        pass


def _json_body(response: Any) -> Dict[str, Any]:
    if isinstance(response, dict):
        return response
    try:
        data = json.loads(bytes(getattr(response, "body", b"") or b"{}"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def media_models(force: bool = False) -> Dict[str, List[str]]:
    """``{"image": [...], "video": [...], "text": [...]}`` — the same lists the chat window shows."""
    cached = _models_cache.get("value")
    if cached is not None and not force and time.monotonic() - float(_models_cache["at"]) < MODELS_TTL:
        return cached
    value: Dict[str, List[str]] = {"image": [], "video": [], "text": []}
    try:
        from . import admin_api
        request, token = _loopback_request("/pg/state", method="GET")
        try:
            data = _json_body(await admin_api.pg_state(request))
        finally:
            _drop_token(token)
        value = {"image": [str(x) for x in data.get("image_models") or [] if str(x)],
                 "video": [str(x) for x in data.get("video_models") or [] if str(x)],
                 "text": [str(row.get("id")) for row in data.get("models") or [] if isinstance(row, dict) and row.get("id")]}
    except Exception as exc:
        log.warning("media models unavailable (%s)", type(exc).__name__)
    _models_cache.update(at=time.monotonic(), value=value)
    return value


def _pref_key(kind: str) -> str:
    return "media_video_model" if kind == "video" else "media_image_model"


def pick_model(kind: str, prefs: Dict[str, Any], lists: Dict[str, List[str]], *, explicit: bool = False) -> str:
    """The desktop ``MemuMedia.pickModel``: "" automatic (pinned first), "off" only by command, else the chosen id."""
    items = list(lists.get("video" if kind == "video" else "image") or [])
    chosen = str(prefs.get(_pref_key(kind)) or "")
    if chosen == "off" and not explicit:
        return ""
    if chosen and chosen != "off" and chosen in items:
        return chosen
    if chosen and chosen != "off" and chosen not in items and not explicit:
        return ""
    pinned = [str(x) for x in prefs.get("pinned_models") or []]
    return next((x for x in pinned if x in items), "") or (items[0] if items else "")


def missing_text(kind: str, prefs: Dict[str, Any], lists: Dict[str, List[str]], lang: str) -> str:
    chosen = str(prefs.get(_pref_key(kind)) or "")
    items = list(lists.get("video" if kind == "video" else "image") or [])
    if chosen and chosen != "off" and chosen not in items:
        return _tr(lang, f"Выбранная модель {'видео' if kind == 'video' else 'изображений'} ({chosen}) сейчас недоступна — "
                         "выберите другую в MemuAI → Настройки → Модели → «Изображения, видео и решения».",
                   f"The chosen {kind} model ({chosen}) is unavailable now — pick another one in MemuAI → Settings → "
                   "Models → “Images, video and decisions”.")
    if kind == "video":
        return _tr(lang, "Нет модели генерации видео. Добавьте в MemuAI Proxy аккаунт Genspark (AI Video) или своё "
                         "подключение с моделью видео (Sora-подобный /videos, Veo в Google AI Studio).",
                   "No video generation model. Add a Genspark account (AI Video) or a custom connection with a video "
                   "model (Sora-style /videos, Veo in Google AI Studio) in MemuAI Proxy.")
    return _tr(lang, "Нет модели генерации изображений. Добавьте в MemuAI Proxy аккаунт Genspark (AI Image) или своё "
                     "подключение с моделью картинок (gpt-image, FLUX, Imagen, Gemini image…).",
               "No image generation model. Add a Genspark account (AI Image) or a custom connection with an image "
               "model (gpt-image, FLUX, Imagen, Gemini image…) in MemuAI Proxy.")


# --------------------------------------------------------------------------- prompt protocol


def prompt_block(image: bool, video: bool) -> str:
    """The in-chat protocol for the main model — the Telegram twin of ``MemuMedia.promptBlock()``."""
    if not image and not video:
        return ("<media_generation>MemuAI has no image or video generation model configured right now. Never claim that "
                "you created, drew or attached a picture or video. If the user asks for one, say briefly that a "
                "generation model can be chosen in MemuAI → Settings → Models (Images, video and decisions) and help "
                "with a text answer meanwhile.</media_generation>")
    kinds = " and ".join(x for x in ("images" if image else "", "video" if video else "") if x)
    blocks = " or ".join(x for x in ("```memuai-image" if image else "", "```memuai-video" if video else "") if x)
    what = "picture or video" if image and video else "picture" if image else "video"
    text = (f"<media_generation>MemuAI can generate {kinds} for this chat; the result is sent to the user in Telegram. "
            f"When the user's message genuinely asks for a NEW {what} (draw, generate, create, show what something "
            f"looks like{', animate, make a clip' if video else ''}), write one short sentence in the user's language "
            f"about what you are creating and append one hidden {blocks} block containing JSON {{\"prompt\": \"...\"}}.\n"
            "The prompt: one self-contained paragraph in English that keeps everything the user specified (subject, "
            "details, style, mood, format, text to render — quoted, in the user's language) and resolves references "
            "to the conversation. Describe what is needed concretely; no generic quality words (8k, masterpiece).\n"
            "Do NOT generate for: tables, charts, diagrams, schemes or code, questions about how generation works, "
            "analysing or describing attached images, finding existing videos. At most one block per answer; never "
            "say the result is ready or describe it as already made.")
    if not image:
        text += " Image generation is not configured: if asked for a picture, say it can be enabled in MemuAI → Settings → Models."
    if not video:
        text += " Video generation is not configured: if asked for a video, say it can be enabled in MemuAI → Settings → Models."
    return text + "</media_generation>"


_BLOCK = re.compile(r"```[ \t]*memuai-(image|video)\s*([\s\S]*?)```", re.I)


def extract(text: str) -> Optional[Dict[str, str]]:
    """The first hidden ``memuai-image`` / ``memuai-video`` block: ``{"kind", "prompt"}``."""
    for match in _BLOCK.finditer(str(text or "")):
        raw = match.group(2).strip()
        try:
            prompt = str(json.loads(raw).get("prompt") or "").strip()
        except Exception:
            prompt = re.sub(r'^\s*\{?\s*"?prompt"?\s*:\s*"?|"?\s*\}?\s*$', "", raw).strip()
        if prompt:
            return {"kind": match.group(1).lower(), "prompt": prompt[:6000]}
    return None


def detect_aspect(text: str, kind: str) -> str:
    value = str(text or "").lower()
    ratio = re.search(r"\b(16:9|9:16|1:1|4:3|3:4|3:2|2:3|21:9)\b", value)
    if ratio:
        return ratio.group(1)
    if re.search(r"вертикал|portrait|vertical|для (?:сторис|stories|reels|tiktok|тикток)|\bstories\b|\breels\b|\btiktok\b", value):
        return "9:16" if kind == "video" else "3:4"
    if re.search(r"квадрат|square|аватар|avatar|иконк|icon\b|логотип|\blogo\b", value):
        return "1:1"
    if re.search(r"горизонтал|landscape|широкоформат|widescreen|panoram|панорам|обои для (?:рабочего|пк|компьютера)|"
                 r"desktop wallpaper", value):
        return "16:9"
    return "16:9" if kind == "video" else "auto"


def context_rows(history: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for row in (history or [])[-8:]:
        if not isinstance(row, dict) or row.get("role") not in ("user", "assistant"):
            continue
        text = re.sub(r"```[\s\S]*?```", " ", str(row.get("content") or ""))
        media = row.get("media") if isinstance(row.get("media"), dict) else None
        if row.get("role") == "assistant" and media and media.get("prompt"):
            text += f" [{'video' if media.get('kind') == 'video' else 'image'} generated: {media.get('prompt')}]"
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            rows.append({"role": str(row["role"]), "text": text[:700]})
    return rows[-6:]


def previous_prompt(history: List[Dict[str, Any]]) -> str:
    for row in list(history or [])[-4:][::-1]:
        media = row.get("media") if isinstance(row, dict) and isinstance(row.get("media"), dict) else None
        if row.get("role") == "assistant" and media and media.get("prompt") and media.get("status") == "done":
            return str(media["prompt"])
    return ""


# --------------------------------------------------------------------------- decisions / autoprompt


async def _complete(model: str, messages: List[Dict[str, Any]], *, fast: bool = False, max_tokens: int = 0,
                    timeout: float = 120.0) -> str:
    from .chat import chat_service
    params: Dict[str, Any] = {"web_search": False, "thinking": False}
    if max_tokens:
        params["max_tokens"] = max_tokens
    if fast:
        params.update({"temperature": 0, "effort": "minimal"})
    parts: List[str] = []

    async def run() -> None:
        stream = chat_service.stream_completion(model, messages, params)
        try:
            async for chunk in stream:
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        parts.append(str(delta["content"]))
                if fast and re.search(r"\b(?:image|video|text)\b", "".join(parts), re.I):
                    return
        finally:
            if hasattr(stream, "aclose"):
                try:
                    await stream.aclose()
                except Exception:
                    pass

    await asyncio.wait_for(run(), timeout)
    return "".join(parts)


async def decide(text: str, prefs: Dict[str, Any], lists: Dict[str, List[str]], *,
                 history: Optional[List[Dict[str, Any]]] = None, attachments: int = 0) -> str:
    """With a Decisions model: ``image`` / ``video`` / ``text`` (heuristic first, one tiny call for unclear ones).
    Without one: always ``text`` — the main model decides through the hidden blocks."""
    from . import media_routing
    model = str(prefs.get("decision_model") or "").strip()
    if not model or model not in (lists.get("text") or []) or not str(text or "").strip():
        return "text"
    last = (history or [])[-1] if history else None
    after_media = bool(last and isinstance(last.get("media"), dict) and last["media"].get("status") == "done")
    kind, confident = media_routing.heuristic(text, has_attachments=bool(attachments), after_media=after_media)
    if kind == "maybe" or not confident:
        rows = context_rows(history or [])
        previous = next((r["text"] for r in reversed(rows) if r["role"] == "assistant"), "")
        user = (f"Previous assistant message: {previous[:240]}\n" if previous else "") + \
               (f"The user attached {attachments} file(s).\n" if attachments else "") + f"Last message: {text[:700]}"
        system = media_routing.decision_system()
        try:
            raw = await _complete(model, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                                  fast=True, max_tokens=256, timeout=12)
            kind = media_routing.parse_decision(raw)
        except Exception as exc:
            log.info("telegram media decision failed (%s)", type(exc).__name__)
            kind = "text" if kind == "maybe" else kind
    if kind not in ("image", "video"):
        return "text"
    return kind if pick_model(kind, prefs, lists) else "text"


async def autoprompt(kind: str, request: str, model: str, *, history: Optional[List[Dict[str, Any]]] = None) -> str:
    """The desktop autoprompt without the web lookup (the bot has no search step of its own)."""
    from . import media_routing
    from .assistant_commands import clean_ai_text
    rows = context_rows(history or [])
    system = media_routing.AUTOPROMPT_SYSTEM.format(
        kind=kind, video_extra=media_routing.AUTOPROMPT_VIDEO_EXTRA if kind == "video" else "", search_rule="")
    user = ""
    if rows:
        user += "Conversation so far (context only):\n" + media_routing.context_text(rows) + "\n\n"
    previous = previous_prompt(history or [])
    if previous:
        user += "Prompt of the previous generation in this chat:\n" + previous + "\n\n"
    user += "User's request:\n" + request
    answer = clean_ai_text(await _complete(model, [{"role": "system", "content": system},
                                                   {"role": "user", "content": user}], timeout=180))
    return media_routing._clean_prompt(re.sub(r"^\s*SEARCH\s*:.*\n?", "", answer, flags=re.I))


# --------------------------------------------------------------------------- generation


class GenerationError(RuntimeError):
    pass


async def generate(kind: str, model: str, prompt: str, *, images: Optional[List[str]] = None, aspect: str = "",
                   system: str = "", stop: Optional[asyncio.Event] = None, lang: str = "ru",
                   on_tick: Optional[Callable[[], Awaitable[None]]] = None) -> List[str]:
    """Run ``admin_api.pg_image`` / ``pg_video`` in-process; the result URLs (https:// or data:)."""
    from . import admin_api, media_routing
    body = {"prompt": prompt[:6000], "model": model, "images": [x for x in images or [] if str(x).startswith("data:image")][:4 if kind == "video" else 10],
            "aspect": aspect or ("16:9" if kind == "video" else "auto"), "system": system[:8000]}
    request, token = _loopback_request("/pg/video" if kind == "video" else "/pg/image", body)
    try:
        response = await (admin_api.pg_video(request) if kind == "video" else admin_api.pg_image(request))
        iterator = getattr(response, "body_iterator", None)
        if iterator is None:
            data = _json_body(response)
            error = data.get("error_en") if lang == "en" and data.get("error_en") else data.get("error")
            raise GenerationError(media_routing.friendly_error(error or "HTTP %s" % getattr(response, "status_code", "?"), lang))
        urls: Optional[List[str]] = None
        error = ""
        buffer = ""
        last_tick = 0.0
        try:
            async for piece in iterator:
                if stop is not None and stop.is_set():
                    raise asyncio.CancelledError()
                buffer += piece.decode("utf-8", "replace") if isinstance(piece, (bytes, bytearray)) else str(piece)
                while "\n\n" in buffer:
                    event, buffer = buffer.split("\n\n", 1)
                    data = event[6:].strip() if event.startswith("data: ") else ""
                    if not data or data == "[DONE]":
                        continue
                    try:
                        item = json.loads(data)
                    except Exception:
                        continue
                    if item.get("status") == "done":
                        urls = [str(u) for u in item.get("urls") or [] if u]
                    elif item.get("status") == "error" or item.get("error"):
                        error = str(item.get("error") or "")
                if on_tick is not None and time.monotonic() - last_tick > 4.5:
                    last_tick = time.monotonic()
                    try:
                        await on_tick()
                    except Exception:
                        pass
        finally:
            if hasattr(iterator, "aclose"):
                try:
                    await iterator.aclose()   # cancels the provider job when we stop early
                except Exception:
                    pass
        if error:
            raise GenerationError(media_routing.friendly_error(error, lang))
        if not urls:
            raise GenerationError(_tr(lang, "Модель не вернула результат", "The model returned no result"))
        return urls
    finally:
        _drop_token(token)


async def fetch_bytes(url: str) -> Tuple[bytes, str]:
    """A generated result as bytes for an upload: ``data:`` URLs, our stored files and https URLs."""
    value = str(url or "")
    match = re.match(r"^data:([\w.+-]+/[\w.+-]+)(?:;[^,]*)?;base64,", value)
    if match:
        payload = value[match.end():]
        if len(payload) > DOWNLOAD_LIMIT * 4 // 3 + 16:
            raise GenerationError("generated file is too large")
        return base64.b64decode(payload, validate=False), match.group(1).lower()
    stored = re.search(r"/api/pg/media/file/([0-9a-f]{64}\.(mp4|webm|mov|png|jpg|webp|gif))(?:\?profile_id=([\w.-]+))?", value)
    if stored:
        from . import media_routing
        path = media_routing.media_folder(stored.group(3) or None) / stored.group(1)
        mime = {"mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime", "png": "image/png",
                "jpg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}[stored.group(2)]
        if not path.is_file() or path.is_symlink():
            raise GenerationError("file not found")
        return await asyncio.to_thread(path.read_bytes), mime
    if not value.startswith("https://"):
        raise GenerationError("unsupported result address")
    from urllib.parse import urlparse
    parsed = urlparse(value)
    if parsed.hostname in ("genspark.ai", "www.genspark.ai") and parsed.path.startswith("/api/files/s/"):
        from . import admin_api
        request, token = _loopback_request("/pg/image/file", method="GET")
        try:
            response = await admin_api.pg_image_file(value, request)
        finally:
            _drop_token(token)
        content = bytes(getattr(response, "body", b"") or b"")
        if getattr(response, "status_code", 500) != 200 or not content:
            raise GenerationError("Genspark file: HTTP %s" % getattr(response, "status_code", "?"))
        return content, str(getattr(response, "media_type", "") or "image/png")
    import httpx
    async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15), follow_redirects=True) as client:
        async with client.stream("GET", value) as resp:
            if resp.status_code != 200:
                raise GenerationError(f"download: HTTP {resp.status_code}")
            chunks: List[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > DOWNLOAD_LIMIT:
                    raise GenerationError("generated file is too large")
                chunks.append(chunk)
            mime = str(resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    return b"".join(chunks), mime or "application/octet-stream"


def stored_url(kind: str, url: str, profile_id: str) -> str:
    """What the chat history keeps: videos become files of the profile (like the desktop app), images stay."""
    if kind == "video" and str(url).startswith("data:video/"):
        from . import media_routing
        try:
            saved = media_routing.store_data_url(url, profile_id)
            if saved:
                return str(saved["url"])
        except Exception as exc:
            log.warning("generated video not stored (%s)", type(exc).__name__)
    return str(url)


async def stored_urls(kind: str, urls: List[str], profile_id: str) -> List[str]:
    """Round 33 (r33d-media): ``stored_url`` + remote pictures downloaded into the profile's files (their links of
    DashScope & co. expire in ~24 h). Genspark links go through the in-process Proxy fetcher (account cookie)."""
    out = [stored_url(kind, url, profile_id) for url in urls]
    if kind == "video":
        return out
    from . import media_cache

    async def genspark(url: str):
        blob, mime = await fetch_bytes(url)
        return 200, blob, mime
    return await media_cache.localize_urls(out, profile_id, genspark_fetch=genspark)


def media_record(kind: str, model: str, request: str, prompt: str, source: str, urls: List[str],
                 *, autoprompted: bool = False, error: str = "") -> Dict[str, Any]:
    """The assistant row fields the desktop chat renders (``MemuMedia.inlineHtml``)."""
    now = int(time.time() * 1000)
    media: Dict[str, Any] = {"kind": kind, "model": model, "request": request[:4000], "source": source,
                             "status": "error" if error else "done", "startedAt": now, "prompt": prompt[:6000],
                             "aspect": detect_aspect(request, kind), "via": "telegram"}
    if autoprompted:
        media["autoprompted"] = True
    if urls:
        media["history"] = [{"prompt": prompt[:6000], "urls": list(urls), "at": now}]
    row: Dict[str, Any] = {"media": media, "images": [] if kind == "video" else list(urls),
                           "videos": list(urls) if kind == "video" else []}
    if error:
        row["error"] = error[:600]
    return row


# --------------------------------------------------------------------------- Telegram bot


def _kind_word(kind: str, lang: str) -> str:
    return _tr(lang, "видео", "video") if kind == "video" else _tr(lang, "изображение", "image")


def _caption(kind: str, model: str, prompt: str, lang: str, short_model: str = "") -> str:
    head = ("🎬 " if kind == "video" else "🎨 ") + html.escape(short_model or model.split("/")[-1])[:60]
    body = html.escape(prompt[:820])
    return f"<b>{head}</b>\n<blockquote expandable>{body}</blockquote>"[:1024]


async def send_result(api: Any, chat_id: str, kind: str, urls: List[str], *, caption: str,
                      reply_markup: Optional[Dict[str, Any]] = None, reply_to: int = 0, lang: str = "ru") -> List[int]:
    """sendPhoto / sendVideo (sendDocument for files over the Bot API limits); message ids sent."""
    sent: List[int] = []
    for index, url in enumerate(urls[:4]):
        blob, mime = await fetch_bytes(url)
        if not blob:
            continue
        last = index == min(len(urls), 4) - 1
        payload: Dict[str, Any] = {"chat_id": chat_id}
        if last:
            payload["caption"] = caption
            payload["parse_mode"] = "HTML"
            if reply_markup:
                payload["reply_markup"] = reply_markup
        if reply_to and index == 0:
            payload["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        ext = (mime.split("/", 1)[1] if "/" in mime else "bin").replace("jpeg", "jpg").replace("quicktime", "mov")
        name = f"memuai-{'video' if kind == 'video' else 'image'}-{index + 1}.{ext}"
        if kind == "video" and mime.startswith("video/") and len(blob) <= UPLOAD_LIMIT:
            method, field = "sendVideo", "video"
            payload["supports_streaming"] = True
        elif kind != "video" and mime in ("image/png", "image/jpeg", "image/webp") and len(blob) <= PHOTO_LIMIT:
            method, field = "sendPhoto", "photo"
        elif len(blob) <= UPLOAD_LIMIT:
            method, field = "sendDocument", "document"
        else:
            raise GenerationError(_tr(lang, "Файл больше 50 МБ — Telegram его не примет. Он сохранён в чате MemuAI.",
                                      "The file is over 50 MB — Telegram does not accept it. It is saved in the MemuAI chat."))
        try:
            result = await api.call(method, essential=True, files={field: (name, blob, mime)}, **payload)
        except Exception:
            if payload.get("parse_mode"):
                payload.pop("parse_mode", None)
                payload["caption"] = re.sub(r"<[^>]+>", "", html.unescape(caption))[:1024]
                result = await api.call(method, essential=True, files={field: (name, blob, mime)}, **payload)
            else:
                raise
        if isinstance(result, dict) and result.get("message_id"):
            sent.append(int(result["message_id"]))
    return sent


async def bot_generate(client: Any, token: str, telegram_chat_id: str, prefs: Dict[str, Any], *, kind: str,
                       request: str, prompt: str = "", source: str = "command", images: Optional[List[str]] = None,
                       reply_to: int = 0, persist_user: Optional[Dict[str, Any]] = None,
                       attach_to: Optional[Tuple[str, str, int]] = None, stop: Optional[asyncio.Event] = None) -> bool:
    """Generate one picture / video for the bot and deliver it.

    ``persist_user`` — save a new turn (user row + media answer) into the selected chat (slash command, router);
    ``attach_to`` — ``(profile_id, chat_id, createdAt)`` of an assistant row that already exists (the main model's
    hidden block): the result is added to that row instead.
    """
    from . import telegram_bot as tb
    from . import media_routing
    lang = tb._lang(prefs)
    lists = await media_models()
    model = pick_model(kind, prefs, lists, explicit=source == "command")
    api = tb._api(client, token)
    if not model:
        await tb._send(client, token, telegram_chat_id, "⚠️ " + missing_text(kind, prefs, lists, lang), reply_to=reply_to)
        return False
    state, session = await tb._io(tb._session, telegram_chat_id)
    profile = await tb._io(tb._profile, session)
    profile_id = str(profile.get("id") or "default")
    temp = bool(session.get("temp"))
    chat = None if temp else await tb._io(tb._current_chat, session)
    history = list((chat or {}).get("messages") or []) if not temp else list(tb._temp_history.get(telegram_chat_id, []))
    rows = context_rows(history)
    stop_markup = {"inline_keyboard": [[{"text": _tr(lang, "⏹ Остановить", "⏹ Stop"), "callback_data": "stop"}]]}
    status_text = (_tr(lang, "🎬 Создаю видео… это может занять несколько минут.", "🎬 Creating the video… this can take a few minutes.")
                   if kind == "video" else _tr(lang, "🎨 Рисую…", "🎨 Drawing…"))
    status_ids = await tb._send(client, token, telegram_chat_id, status_text, stop_markup, reply_to=reply_to, silent=True)
    status_id = status_ids[0] if status_ids else 0
    action = "upload_video" if kind == "video" else "upload_photo"

    async def tick() -> None:
        await tb._call(client, token, "sendChatAction", chat_id=telegram_chat_id, action=action)

    final_prompt, autoprompted, note = prompt, False, ""
    error = ""
    urls: List[str] = []
    try:
        await tick()
    except Exception:
        pass
    try:
        if prefs.get("media_autoprompt"):
            thinker = str(await tb._io(tb._model_for, session, prefs) or "")
            if thinker and thinker in (lists.get("text") or []):
                try:
                    better = await autoprompt(kind, prompt or request, thinker, history=history)
                    if better:
                        final_prompt, autoprompted = better, True
                except Exception as exc:
                    note = _tr(lang, "Не удалось улучшить промпт — использую исходный.", "Could not improve the prompt — using the original.")
                    log.info("telegram autoprompt failed (%s)", type(exc).__name__)
        if not final_prompt:
            final_prompt = media_routing.generation_prompt(kind, request, rows=rows, previous_prompt=previous_prompt(history))
        if stop is not None and stop.is_set():
            raise asyncio.CancelledError()
        raw_urls = await asyncio.wait_for(generate(
            kind, model, final_prompt, images=images, aspect=detect_aspect(request, kind),
            system=media_routing.generation_system(kind, rows), stop=stop, lang=lang, on_tick=tick), GENERATION_TIMEOUT)
        urls = await stored_urls(kind, raw_urls, profile_id)
    except asyncio.CancelledError:
        if status_id:
            await tb._delete_quietly(client, token, telegram_chat_id, status_id)
        await tb._send(client, token, telegram_chat_id, _tr(lang, "⏹ Генерация остановлена.", "⏹ Generation stopped."), silent=True)
        return False
    except asyncio.TimeoutError:
        error = _tr(lang, "Генерация не закончилась вовремя", "The generation did not finish in time")
    except Exception as exc:
        error = str(exc)[:600] or type(exc).__name__
        log.warning("telegram %s generation failed (%s)", kind, type(exc).__name__)
    if status_id:
        await tb._delete_quietly(client, token, telegram_chat_id, status_id)
    session_state, session = await tb._io(tb._session, telegram_chat_id)
    session["last_media"] = {"kind": kind, "request": request[:3000], "prompt": final_prompt[:6000], "model": model}
    await tb._io(tb._save_session, session_state)
    again = {"inline_keyboard": [[{"text": _tr(lang, "🔁 Ещё вариант", "🔁 Another one"), "callback_data": "media:again"}]]}
    if error:
        await tb._send(client, token, telegram_chat_id, "⚠️ " + _tr(lang, "Не удалось создать ", "Could not create the ")
                       + _kind_word(kind, lang) + ": " + error,
                       {"inline_keyboard": [[{"text": "↻ " + _tr(lang, "Повторить", "Retry"), "callback_data": "media:again"}]]},
                       reply_to=reply_to)
    else:
        try:
            await send_result(api, telegram_chat_id, kind, urls, reply_to=reply_to, reply_markup=again, lang=lang,
                              caption=_caption(kind, model, final_prompt, lang, tb._short_model(model, prefs, 40)))
            if note:
                await tb._send(client, token, telegram_chat_id, "ℹ️ " + note, silent=True)
        except Exception as exc:
            log.warning("telegram media delivery failed (%s)", type(exc).__name__)
            await tb._send(client, token, telegram_chat_id, "⚠️ " + (str(exc)[:500] if isinstance(exc, GenerationError)
                           else _tr(lang, "Не удалось отправить файл в Telegram.", "Could not send the file to Telegram.")))
    record = media_record(kind, model, request, final_prompt, source, urls,
                          autoprompted=autoprompted, error=error)
    if temp:
        rows_temp = list(tb._temp_history.get(telegram_chat_id, []))
        if persist_user is not None:
            rows_temp.append({"role": "user", "content": str(persist_user.get("content") or "")})
        rows_temp.append({"role": "assistant", "content": f"[{kind} generated: {final_prompt[:300]}]" if urls else ""})
        tb._temp_history[telegram_chat_id] = rows_temp[-200:]
        return not error
    try:
        if attach_to is not None:
            await tb._io(_attach, attach_to, record)
        elif persist_user is not None:
            await tb._io(_persist_new, telegram_chat_id, session, profile_id, persist_user, record, model, request)
    except Exception as exc:
        log.warning("telegram media turn not saved (%s)", type(exc).__name__)
    return not error


def _attach(target: Tuple[str, str, int], record: Dict[str, Any]) -> None:
    from .pgdata import patch_chat
    profile_id, chat_id, created = target

    def apply(row: Dict[str, Any]) -> bool:
        for message in reversed(list(row.get("messages") or [])):
            if message.get("role") == "assistant" and int(message.get("createdAt") or 0) == int(created):
                message.update({k: v for k, v in record.items() if k != "error"})
                if record.get("error"):
                    message["mediaError"] = record["error"]
                return True
        return False

    patch_chat(chat_id, apply, profile_id)


def _persist_new(telegram_chat_id: str, session: Dict[str, Any], profile_id: str, user_row: Dict[str, Any],
                 record: Dict[str, Any], model: str, request: str) -> None:
    from . import telegram_bot as tb
    from .pgdata import upsert_chat
    now = int(time.time() * 1000)
    user = dict(user_row, createdAt=int(user_row.get("createdAt") or now), via="telegram")
    assistant = dict(record, role="assistant", content="", model=model, createdAt=now + 1)
    chat = tb._current_chat(session)
    if chat is None:
        chat = {"id": "tg-" + secrets.token_hex(8), "title": re.sub(r"\s+", " ", request).strip()[:48] or "Telegram",
                "messages": [], "mode": "chat", "updated": now, "telegram": True, "created": now, "_new": True}
        state, fresh = tb._session(telegram_chat_id)
        fresh.setdefault("chat_by_profile", {})[profile_id] = chat["id"]
        tb._save_session(state)
        chat.pop("_new", None)
        chat["messages"] = [user, assistant]
        upsert_chat(chat, profile_id)
        return
    tb._persist_turn(profile_id, chat, user, assistant, False, False)


async def bot_command(client: Any, token: str, telegram_chat_id: str, prefs: Dict[str, Any], command: str,
                      argument: str, *, reply_to: int = 0, message: Optional[Dict[str, Any]] = None) -> None:
    """``/image …`` / ``/video …`` (a photo sent with the command as its caption is the reference picture)."""
    from . import telegram_bot as tb
    lang = tb._lang(prefs)
    kind = COMMANDS.get(command, "image")
    request = str(argument or "").strip()
    images: List[str] = []
    if request and message and (message.get("photo") or (message.get("document") or {}).get("mime_type", "").startswith("image/")):
        try:
            from . import telegram_media
            collected = await telegram_media.collect(tb._api(client, token), message, prefs, lang)
            images = list(collected.images)
        except Exception as exc:
            log.info("telegram media reference not read (%s)", type(exc).__name__)
    if not request:
        example = _tr(lang, "/video закат над морем, медленный пролёт камеры", "/video a sunset over the sea, slow camera flight") \
            if kind == "video" else _tr(lang, "/image кот-астронавт в стиле акварели", "/image a cat astronaut, watercolour")
        await tb._send(client, token, telegram_chat_id, _tr(
            lang, f"Напишите, что создать, после команды. Например:\n{example}",
            f"Write what to create after the command. For example:\n{example}"), reply_to=reply_to)
        return
    if telegram_chat_id in tb._generations:
        await tb._send(client, token, telegram_chat_id, _tr(
            lang, "⏳ Ещё отвечаю на предыдущее сообщение — дождитесь ответа или остановите его.",
            "⏳ Still answering the previous message — wait for it or stop it."),
            {"inline_keyboard": [[{"text": _tr(lang, "⏹ Остановить", "⏹ Stop"), "callback_data": "stop"}]]})
        return
    generation = tb._Generation(telegram_chat_id)
    tb._generations[telegram_chat_id] = generation
    try:
        await bot_generate(client, token, telegram_chat_id, prefs, kind=kind, request=request, source="command",
                           images=images, reply_to=reply_to, stop=generation.stop,
                           persist_user={"role": "user", "content": f"/{kind} {request}", "mediaCommand": kind})
    finally:
        if tb._generations.get(telegram_chat_id) is generation:
            tb._generations.pop(telegram_chat_id, None)


async def bot_again(client: Any, token: str, telegram_chat_id: str, prefs: Dict[str, Any]) -> None:
    """«🔁 Another one» / «↻ Retry» under a result: the same prompt once more, as a new turn."""
    from . import telegram_bot as tb
    _, session = await tb._io(tb._session, telegram_chat_id)
    last = session.get("last_media") if isinstance(session.get("last_media"), dict) else None
    lang = tb._lang(prefs)
    if not last or not last.get("request"):
        await tb._send(client, token, telegram_chat_id, _tr(lang, "Нечего повторить — используйте /image или /video.",
                                                            "Nothing to repeat — use /image or /video."))
        return
    if telegram_chat_id in tb._generations:
        await tb._send(client, token, telegram_chat_id, _tr(lang, "⏳ Дождитесь текущего ответа.", "⏳ Wait for the current answer."))
        return
    kind = "video" if last.get("kind") == "video" else "image"
    generation = tb._Generation(telegram_chat_id)
    tb._generations[telegram_chat_id] = generation
    try:
        await bot_generate(client, token, telegram_chat_id, prefs, kind=kind, request=str(last.get("request") or ""),
                           prompt=str(last.get("prompt") or ""), source="command", stop=generation.stop,
                           persist_user={"role": "user", "content": f"/{kind} {last.get('request')}", "mediaCommand": kind})
    finally:
        if tb._generations.get(telegram_chat_id) is generation:
            tb._generations.pop(telegram_chat_id, None)


async def bot_route(client: Any, token: str, telegram_chat_id: str, prompt: str, prefs: Dict[str, Any], *,
                    reply_to: int, images: Optional[List[str]], stop: asyncio.Event,
                    attachments: Optional[List[Dict[str, Any]]] = None) -> bool:
    """Before the normal answer: the Decisions model may route this message to image / video. True = handled."""
    if not str(prefs.get("decision_model") or "").strip():
        return False
    from . import telegram_bot as tb
    lists = await media_models()
    if not (pick_model("image", prefs, lists) or pick_model("video", prefs, lists)):
        return False
    _, session = await tb._io(tb._session, telegram_chat_id)
    chat = None if session.get("temp") else await tb._io(tb._current_chat, session)
    history = list((chat or {}).get("messages") or [])
    kind = await decide(prompt, prefs, lists, history=history, attachments=len(images or []))
    if kind not in ("image", "video"):
        return False
    user_row: Dict[str, Any] = {"role": "user", "content": prompt}
    if attachments:
        user_row["attachments"] = [dict(item) for item in attachments][:8]
    await bot_generate(client, token, telegram_chat_id, prefs, kind=kind, request=prompt, source="decision",
                       images=images, reply_to=reply_to, stop=stop, persist_user=user_row)
    return True


async def prompt_section(prefs: Dict[str, Any]) -> str:
    """The ``<media_generation>`` section for the bot's system prompt."""
    lists = await media_models()
    return prompt_block(bool(pick_model("image", prefs, lists)), bool(pick_model("video", prefs, lists)))


# --------------------------------------------------------------------------- Mini App (tgapp_chat / tgapp_api)

APP_INLINE_LIMIT = 3_000_000
APP_FILE_LIMIT = 40 * 1024 * 1024
_COMMAND_RE = re.compile(r"^/(image|img|picture|video)(?:@\w+)?(?:\s+([\s\S]*))?$", re.I)


def parse_command(text: str) -> Optional[Tuple[str, str]]:
    """``"/video waves"`` → ``("video", "waves")``; None for any other text."""
    match = _COMMAND_RE.match(str(text or "").strip())
    if not match:
        return None
    return ("video" if match.group(1).lower() == "video" else "image"), (match.group(2) or "").strip()


def prompt_block_cached(prefs: Dict[str, Any]) -> str:
    """``prompt_block`` from the model lists ``media_models()`` fetched last (``chats.send`` warms them)."""
    lists = _models_cache.get("value") or {"image": [], "video": []}
    return prompt_block(bool(pick_model("image", prefs, lists)), bool(pick_model("video", prefs, lists)))


def app_media_out(row: Dict[str, Any]) -> Dict[str, Any]:
    """The media fields of a stored assistant row for the Mini App (big inline data is left out)."""
    media = row.get("media") if isinstance(row.get("media"), dict) else None
    if row.get("role") != "assistant" or (not media and not row.get("images") and not row.get("videos")):
        return {}

    def usable(url: Any) -> str:
        value = str(url or "")
        if value.startswith("data:"):
            return value if len(value) < APP_INLINE_LIMIT else ""
        return value if value.startswith(("https://", "/api/pg/media/file/")) else ""

    out: Dict[str, Any] = {"images": [u for u in map(usable, (row.get("images") or [])[:8]) if u and u != "__skeleton__"],
                           "videos": [u for u in map(usable, (row.get("videos") or [])[:4]) if u]}
    if media:
        out["media"] = {key: media.get(key) for key in ("kind", "model", "status", "source", "autoprompted")
                        if media.get(key) is not None}
        out["media"]["prompt"] = str(media.get("prompt") or media.get("request") or "")[:2000]
    if row.get("mediaError") or (media and media.get("status") == "error" and row.get("error")):
        out["mediaError"] = str(row.get("mediaError") or row.get("error") or "")[:600]
    return out


async def app_media_file(url: str, profile_id: str) -> Dict[str, Any]:
    """``media.file``: a generated file of this profile (``generated_media``) as a data URL for <video>/<img>."""
    value = str(url or "")
    stored = re.search(r"/api/pg/media/file/([0-9a-f]{64}\.(?:mp4|webm|mov|png|jpg|webp|gif))", value)
    if not stored:   # https results load in the browser directly; the server never fetches other addresses here
        raise GenerationError("unsupported address")
    value = f"/api/pg/media/file/{stored.group(1)}?profile_id={profile_id or 'default'}"
    blob, mime = await fetch_bytes(value)
    if len(blob) > APP_FILE_LIMIT:
        raise GenerationError("file too large")
    return {"data": f"data:{mime};base64," + base64.b64encode(blob).decode("ascii"), "mime": mime, "size": len(blob)}


async def app_generate(job: Any, kind: str, *, request: str, prompt: str = "", source: str, history: List[Dict[str, Any]],
                       images: Optional[List[str]], profile_id: str, lang: str, prefs: Dict[str, Any],
                       thinker: str = "") -> Dict[str, Any]:
    """One generation for a Mini App job: emits ``media`` progress events, returns the assistant row fields."""
    from . import media_routing
    lists = await media_models()
    model = pick_model(kind, prefs, lists, explicit=source == "command")
    if not model:
        return media_record(kind, "", request, prompt or request, source, [], error=missing_text(kind, prefs, lists, lang))
    rows = context_rows(history)
    job.emit("media", media_kind=kind, status="prompting" if prefs.get("media_autoprompt") else "generating", model=model)
    final_prompt, autoprompted = prompt, False
    if prefs.get("media_autoprompt") and thinker and thinker in (lists.get("text") or []):
        try:
            better = await autoprompt(kind, prompt or request, thinker, history=history)
            if better:
                final_prompt, autoprompted = better, True
        except Exception as exc:
            log.info("mini app autoprompt failed (%s)", type(exc).__name__)
        job.emit("media", media_kind=kind, status="generating", model=model, prompt=final_prompt[:2000])
    if not final_prompt:
        final_prompt = media_routing.generation_prompt(kind, request, rows=rows, previous_prompt=previous_prompt(history))
    try:
        raw = await asyncio.wait_for(generate(kind, model, final_prompt, images=images, aspect=detect_aspect(request, kind),
                                              system=media_routing.generation_system(kind, rows), lang=lang),
                                     GENERATION_TIMEOUT)
        urls = await stored_urls(kind, raw, profile_id)
        return media_record(kind, model, request, final_prompt, source, urls, autoprompted=autoprompted)
    except asyncio.TimeoutError:
        error = _tr(lang, "Генерация не закончилась вовремя", "The generation did not finish in time")
    except Exception as exc:
        error = str(exc)[:600] or type(exc).__name__
    return media_record(kind, model, request, final_prompt, source, [], autoprompted=autoprompted, error=error)


async def app_hidden_block(job: Any, raw: str, *, request: str, history: List[Dict[str, Any]], profile_id: str,
                           lang: str) -> Optional[Dict[str, Any]]:
    """After a Mini App answer: the main model's hidden ``memuai-image`` / ``memuai-video`` block → generation."""
    action = extract(raw)
    if not action:
        return None
    from .pgdata import load_preferences
    prefs = load_preferences()
    return await app_generate(job, action["kind"], request=request, prompt=action["prompt"], source="model",
                              history=history, images=None, profile_id=profile_id, lang=lang, prefs=prefs)


async def app_command_job(job: Any, chat: Dict[str, Any], history: List[Dict[str, Any]], user_row: Dict[str, Any],
                          kind: str, request: str, images: List[str], model: str, profile_id: str, lang: str,
                          temp: bool, meme_id: str) -> None:
    """``/image`` / ``/video`` in the Mini App: no text answer, the result is the assistant message."""
    from . import tgapp_chat as tchat
    from .pgdata import load_preferences
    started = time.monotonic()
    assistant: Optional[Dict[str, Any]] = None
    error = ""
    stopped = False
    try:
        if not request:
            example = "/video …" if kind == "video" else "/image …"
            raise GenerationError(_tr(lang, f"Напишите, что создать, после команды: {example}",
                                      f"Write what to create after the command: {example}"))
        extra = await app_generate(job, kind, request=request, source="command", history=history, images=images,
                                   profile_id=profile_id, lang=lang, prefs=load_preferences(), thinker=model)
        assistant = dict(extra, role="assistant", content="", model=extra["media"].get("model") or model,
                         createdAt=tchat.now_ms(), duration=round(time.monotonic() - started, 1))
        if extra.get("error"):
            error = str(extra["error"])
    except asyncio.CancelledError:
        stopped = True
    except Exception as exc:
        error = str(exc)[:600] or type(exc).__name__
    saved = None
    if not temp and (assistant is not None or not error):
        try:
            saved = await asyncio.to_thread(tchat._persist, chat, user_row, assistant, "", model, profile_id, meme_id)
        except Exception as exc:
            log.warning("mini app media turn not saved (%s)", type(exc).__name__)
    if assistant is not None:
        job.emit("message", message={k: v for k, v in assistant.items() if k != "error"} | app_media_out(assistant))
    if error:
        job.emit("error", message=error)
    job.emit("done", stopped=stopped, chat_id=chat["id"], ok=not error, title_pending=False)
    job.finish()
