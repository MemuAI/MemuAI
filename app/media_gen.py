"""Round 29 (r29b-media-routing): image and video generation through Custom (OpenAI-compatible) connections.

Runs inside MemuAI Proxy. ``admin_api.pg_image`` / ``pg_video`` call these helpers for Custom connections; Genspark
keeps its own AI Image / AI Video agent (``providers/genspark.py``).

What is supported (all paths are mock-tested in ``tests/round29_media_routing_test.py``):

* images — OpenAI ``POST /images/generations`` (``data[].url`` / ``data[].b64_json``);
  chat-native image models (Gemini *-image / nano-banana / *-image-preview on OpenRouter-style gateways) through
  ``POST /chat/completions`` with ``modalities: ["image", "text"]`` — these also accept reference images;
  Google AI Studio (``generativelanguage.googleapis.com``) chat image models through native ``:generateContent``;
* video — OpenAI ``POST /videos`` → poll ``GET /videos/{id}`` → ``GET /videos/{id}/content`` (Sora-style API, also
  used by many compatible gateways); Google AI Studio Veo through ``:predictLongRunning`` → operation polling.

Results are returned as ``https://`` URLs when the API gives public ones, otherwise as ``data:`` URLs (MemuAI stores
them as files in the chat's media folder). Nothing here logs keys or prompts.
"""
from __future__ import annotations

import asyncio
import base64
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from .providers.base import ProviderError, ProviderNetworkError, ProviderServerError

# Video generation names; the Studio module and the capability heuristics use the same family list.
VIDEO_HINTS = ("sora", "veo", "kling", "runway", "hailuo", "seedance", "wan2", "wan-2", "luma", "pika",
               "cogvideo", "hunyuan-video", "hunyuanvideo", "ltx-video", "mochi", "video")
# Image generation names (kept in sync with admin_api.pg_state image_hints).
IMAGE_HINTS = ("gpt-image", "dall-e", "dalle", "imagen", "flux", "seedream", "recraft", "ideogram", "qwen-image",
               "nano-banana", "imagegen", "stable-diffusion", "sdxl", "kolors", "hidream", "midjourney",
               "flash-image", "image-preview", "-image", "imagine", "t2i", "wanx", "cogview", "kandinsky",
               "dreamina", "jimeng", "hunyuan-image", "playground-v")
# Chat-native image models: they answer /chat/completions with pictures instead of serving /images/generations.
_CHAT_IMAGE = re.compile(r"gemini[\w.\-]*image|nano-banana|image-preview|flash-image|gpt-[\w.]*-image(?!-\d)|"
                         r"grok-[\w.\-]*image", re.I)
_NOT_VIDEO = re.compile(r"(?:^|[-_/])(?:vl|vision|understanding|llava|chat|instruct|omni|"
                        r"video-?(?:llm|chat|analysis|caption|llava))(?:$|[-_/\d])", re.I)

MAX_MEDIA_BYTES = 160 * 1024 * 1024
VIDEO_POLL_SECONDS = 4.0
VIDEO_TIMEOUT_SECONDS = 20 * 60


def is_video_name(name: str) -> bool:
    low = str(name or "").lower()
    if _NOT_VIDEO.search(low):
        return False
    if re.search(r"(?:^|[-_/.])video(?:$|[-_/.\d])", low):
        return True
    return any(h in low for h in VIDEO_HINTS if h != "video")


def is_image_name(name: str) -> bool:
    low = str(name or "").lower()
    if is_video_name(low):
        return False
    if re.search(r"(?:^|[-_/])(?:vl|vision)(?:$|[-_/\d])", low):
        return False
    return any(h in low for h in IMAGE_HINTS)


_WRONG_ENDPOINT = re.compile(r"images/generations|chat/completions|endpoint[_ ]?(?:mismatch|not supported)|"
                             r"not (?:a|supported for) chat|не чата|does not support (?:chat|image generation)|"
                             r"model_endpoint_mismatch|only (?:supports?|available) (?:via|through|for)", re.I)


def wrong_endpoint(exc: BaseException) -> bool:
    """Round 33: the gateway says this model lives on the other endpoint (chat ↔ /images/generations)."""
    status = int(getattr(exc, "status", 0) or 0)
    return status in (0, 400, 404, 405, 422) and bool(_WRONG_ENDPOINT.search(str(exc)))


def is_chat_image_model(name: str) -> bool:
    return bool(_CHAT_IMAGE.search(str(name or "")))


def declared(account: Any, field: str) -> List[str]:
    raw = str((getattr(account, "fields", {}) or {}).get(field, "") or "")
    return [part.strip() for part in re.split(r"[,\n;]", raw) if part.strip()]


def account_prefix(account: Any) -> str:
    return str((getattr(account, "fields", {}) or {}).get("name", "")).strip().lower().replace(" ", "-")


def _google_root(base: str) -> str:
    """``https://generativelanguage.googleapis.com`` when the connection points at Google AI Studio, else ''."""
    try:
        parsed = urllib.parse.urlparse(base)
    except Exception:
        return ""
    if parsed.scheme == "https" and parsed.hostname == "generativelanguage.googleapis.com":
        return "https://generativelanguage.googleapis.com"
    return ""


def _api_key(provider: Any, account: Any) -> str:
    headers = provider._headers(account)
    auth = str(headers.get("Authorization") or "")
    return auth[7:] if auth.startswith("Bearer ") else ""


async def _session(provider: Any, account: Any):
    from .providers import http
    base = provider.base_of(account)
    return await http.get_session(provider.proxy_for(account), impersonate=not base.startswith("http://"), url=base)


def _timeout() -> float:
    try:
        from .config import config_store
        return float(config_store.settings.request_timeout or 300)
    except Exception:
        return 300.0


async def _raise(provider: Any, resp: Any) -> None:
    from .providers.base import retry_after_seconds
    await provider._raise_for_status(resp.status_code, str(getattr(resp, "text", "") or ""),
                                     retry_after=retry_after_seconds(getattr(resp, "headers", None)))


def _data_url(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def _sniff_video(raw: bytes, fallback: str = "video/mp4") -> str:
    if raw[4:8] == b"ftyp":
        return "video/mp4"
    if raw[:4] == b"\x1aE\xdf\xa3":
        return "video/webm"
    return fallback


# --------------------------------------------------------------------------- images


def _images_from_message(message: Dict[str, Any]) -> List[str]:
    """Pictures in a chat answer: OpenRouter ``message.images``, content parts, Markdown data/https images."""
    urls: List[str] = []

    def take(value: Any) -> None:
        if isinstance(value, dict):
            inner = value.get("image_url") or value.get("url") or value.get("image")
            if isinstance(inner, dict):
                inner = inner.get("url")
            if isinstance(inner, str) and (inner.startswith("data:image") or inner.startswith("https://")):
                urls.append(inner)
            elif value.get("b64_json"):
                urls.append("data:image/png;base64," + str(value["b64_json"]))
        elif isinstance(value, str) and (value.startswith("data:image") or value.startswith("https://")):
            urls.append(value)

    for item in message.get("images") or []:
        take(item)
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and str(part.get("type") or "") in ("image_url", "output_image", "image"):
                take(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                content_text = str(part.get("text") or "")
                urls.extend(re.findall(r"!\[[^\]]*\]\(((?:data:image/[\w.+-]+;base64,[A-Za-z0-9+/=]+)|https://[^)\s]+)\)",
                                       content_text))
    elif isinstance(content, str):
        urls.extend(re.findall(r"!\[[^\]]*\]\(((?:data:image/[\w.+-]+;base64,[A-Za-z0-9+/=]+)|https://[^)\s]+)\)", content))
    return list(dict.fromkeys(urls))


def _message_text(message: Dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):
        return " ".join(str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") == "text").strip()
    return str(content or "").strip()


async def _chat_image(provider: Any, account: Any, prompt: str, model: str, aspect: str,
                      images: List[str], system: str = "") -> List[str]:
    root = _google_root(provider.base_of(account))
    if root:
        return await _gemini_native_image(provider, account, root, prompt, model, aspect, images, system)
    parts: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    parts += [{"type": "image_url", "image_url": {"url": url}} for url in images[:10]]
    messages: List[Dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": parts if images else prompt})
    body: Dict[str, Any] = {"model": model, "messages": messages, "modalities": ["image", "text"], "stream": False}
    if aspect and aspect != "auto":
        body["image_config"] = {"aspect_ratio": aspect}
    try:
        session = await _session(provider, account)
        resp = await session.post(f"{provider.base_of(account)}/chat/completions", headers=provider._headers(account),
                                  json=body, timeout=_timeout())
    except Exception as exc:
        raise ProviderNetworkError(f"Network error during generation: {type(exc).__name__}: {exc}")
    if resp.status_code != 200:
        await _raise(provider, resp)
    try:
        payload = resp.json() or {}
    except Exception as exc:
        raise ProviderServerError(f"Could not parse the generation response: {type(exc).__name__}")
    message = ((payload.get("choices") or [{}])[0] or {}).get("message") or {}
    urls = _images_from_message(message)
    if not urls:
        said = _message_text(message)[:240]
        raise ProviderServerError("The model answered without an image" + (f": {said}" if said else
                                  " (it may not support image output through this API)."))
    return urls


async def _gemini_native_image(provider: Any, account: Any, root: str, prompt: str, model: str, aspect: str,
                               images: List[str], system: str = "") -> List[str]:
    parts: List[Dict[str, Any]] = [{"text": prompt}]
    for url in images[:10]:
        match = re.match(r"^data:(image/[\w.+-]+);base64,(.+)$", url, re.S)
        if match:
            parts.append({"inlineData": {"mimeType": match[1], "data": match[2]}})
    body: Dict[str, Any] = {"contents": [{"role": "user", "parts": parts}],
                            "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]}}
    if aspect and aspect != "auto":
        body["generationConfig"]["imageConfig"] = {"aspectRatio": aspect}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    name = model.split("/", 1)[1] if model.startswith("models/") else model
    try:
        session = await _session(provider, account)
        resp = await session.post(f"{root}/v1beta/models/{urllib.parse.quote(name, safe='.-_')}:generateContent",
                                  headers={"x-goog-api-key": _api_key(provider, account),
                                           "Content-Type": "application/json"},
                                  json=body, timeout=_timeout())
    except Exception as exc:
        raise ProviderNetworkError(f"Network error during generation: {type(exc).__name__}: {exc}")
    if resp.status_code != 200:
        await _raise(provider, resp)
    payload = resp.json() or {}
    urls: List[str] = []
    said = ""
    for candidate in payload.get("candidates") or []:
        for part in ((candidate or {}).get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data") or {}
            if inline.get("data"):
                urls.append(f"data:{inline.get('mimeType') or inline.get('mime_type') or 'image/png'};base64,{inline['data']}")
            elif part.get("text"):
                said += str(part["text"])
    if not urls:
        block = (payload.get("promptFeedback") or {}).get("blockReason")
        raise ProviderServerError("Gemini returned no image" + (f" (blocked: {block})" if block else "")
                                  + (f": {said[:240]}" if said else "."))
    return urls


async def generate_image(provider: Any, account: Any, prompt: str, model: str, aspect: str = "auto",
                         images: Optional[List[str]] = None, system: str = "") -> List[str]:
    """Custom connection: chat-native image models go through chat; others through /images/generations."""
    images = [str(x) for x in (images or []) if str(x).startswith("data:image")]
    if is_chat_image_model(model) or _google_root(provider.base_of(account)) and "imagen" not in model.lower():
        try:
            return await _chat_image(provider, account, prompt, model, aspect, images, system)
        except ProviderError as exc:
            # Round 33 (r33-models): the name looked chat-native («grok-imagine-image-2.0»), but this gateway serves it
            # only through /images/generations («model_endpoint_mismatch»): try the Images API instead of failing.
            if images or _google_root(provider.base_of(account)) or not wrong_endpoint(exc):
                raise
            return await provider.generate_images(account, prompt, model, aspect, [])
    if images:
        # The Images API has no standard way to pass a reference; a chat image model can take it.
        raise ProviderServerError(
            "This image model works through /images/generations, which has no standard way to take a reference "
            "picture. Send the prompt without an attachment or pick a chat image model (Gemini image, nano-banana).",
            400)
    try:
        return await provider.generate_images(account, prompt, model, aspect, [])
    except ProviderServerError as exc:
        # Some gateways serve every model only through chat completions.
        if getattr(exc, "status", 0) in (404, 405) or wrong_endpoint(exc):
            return await _chat_image(provider, account, prompt, model, aspect, [], system)
        raise


# --------------------------------------------------------------------------- video


def _video_size(aspect: str) -> str:
    return {"16:9": "1280x720", "9:16": "720x1280", "1:1": "1024x1024", "4:3": "1024x768",
            "3:4": "768x1024"}.get(aspect, "1280x720")


def _urls_in(value: Any, depth: int = 0) -> List[str]:
    found: List[str] = []
    if depth > 6:
        return found
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("url", "video_url", "download_url", "uri") and isinstance(child, str) \
                    and child.startswith("https://"):
                found.append(child)
            else:
                found.extend(_urls_in(child, depth + 1))
    elif isinstance(value, list):
        for child in value:
            found.extend(_urls_in(child, depth + 1))
    return found


async def _download(session: Any, url: str, headers: Dict[str, str]) -> Tuple[bytes, str]:
    resp = await session.get(url, headers=headers, timeout=_timeout(), allow_redirects=True)
    if resp.status_code != 200:
        raise ProviderServerError(f"Could not download the generated video (HTTP {resp.status_code})",
                                  resp.status_code)
    raw = bytes(resp.content or b"")
    if not raw:
        raise ProviderServerError("The generated video is empty")
    if len(raw) > MAX_MEDIA_BYTES:
        raise ProviderServerError("The generated video is larger than 160 MB", 413)
    mime = str((getattr(resp, "headers", {}) or {}).get("content-type") or "").split(";", 1)[0]
    return raw, mime if mime.startswith("video/") else _sniff_video(raw)


async def _openai_video(provider: Any, account: Any, prompt: str, model: str, aspect: str,
                        seconds: int, images: List[str], on_status=None) -> List[str]:
    base = provider.base_of(account)
    session = await _session(provider, account)
    body: Dict[str, Any] = {"model": model, "prompt": prompt, "size": _video_size(aspect)}
    if seconds:
        body["seconds"] = str(seconds)
    if images:
        body["input_reference"] = images[0]
    headers = provider._headers(account)
    try:
        resp = await session.post(f"{base}/videos", headers=headers, json=body, timeout=_timeout())
        if resp.status_code in (400, 415, 422) and "multipart" in str(getattr(resp, "text", "")).lower():
            # The reference OpenAI API takes multipart form data.
            from curl_cffi import CurlMime
            form = CurlMime()
            for key, value in body.items():
                if key != "input_reference":
                    form.addpart(name=key, data=str(value).encode("utf-8"))
            plain = {k: v for k, v in headers.items() if k.lower() != "content-type"}
            resp = await session.post(f"{base}/videos", headers=plain, multipart=form, timeout=_timeout())
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderNetworkError(f"Network error during video generation: {type(exc).__name__}: {exc}")
    if resp.status_code not in (200, 201, 202):
        await _raise(provider, resp)
    job = resp.json() or {}
    direct = _urls_in(job) if str(job.get("status") or "").lower() in ("completed", "succeeded", "success") else []
    if direct:
        return direct
    job_id = str(job.get("id") or job.get("task_id") or "")
    if not job_id:
        raise ProviderServerError("The video API accepted the request but returned no job id")
    started = time.time()
    status = str(job.get("status") or "queued")
    while True:
        lowered = status.lower()
        if lowered in ("completed", "succeeded", "success", "done"):
            break
        if lowered in ("failed", "error", "cancelled", "canceled", "rejected"):
            err = job.get("error")
            detail = err.get("message") if isinstance(err, dict) else err
            raise ProviderServerError("Video generation failed" + (f": {str(detail)[:300]}" if detail else ""))
        if time.time() - started > VIDEO_TIMEOUT_SECONDS:
            raise ProviderServerError("Video generation did not finish in 20 minutes")
        await asyncio.sleep(VIDEO_POLL_SECONDS)
        try:
            poll = await session.get(f"{base}/videos/{urllib.parse.quote(job_id, safe='')}", headers=headers,
                                     timeout=60)
        except Exception as exc:
            raise ProviderNetworkError(f"Network error while waiting for the video: {type(exc).__name__}")
        if poll.status_code != 200:
            await _raise(provider, poll)
        job = poll.json() or {}
        status = str(job.get("status") or status)
        if on_status:
            try:
                on_status({"progress": job.get("progress"), "state": status})
            except Exception:
                pass
    direct = _urls_in(job)
    if direct:
        return direct
    raw, mime = await _download(session, f"{base}/videos/{urllib.parse.quote(job_id, safe='')}/content",
                                {k: v for k, v in headers.items() if k.lower() != "content-type"})
    return [_data_url(raw, mime)]


async def _veo_video(provider: Any, account: Any, root: str, prompt: str, model: str, aspect: str,
                     seconds: int, images: List[str], on_status=None) -> List[str]:
    key = _api_key(provider, account)
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    session = await _session(provider, account)
    instance: Dict[str, Any] = {"prompt": prompt}
    if images:
        match = re.match(r"^data:(image/[\w.+-]+);base64,(.+)$", images[0], re.S)
        if match:
            instance["image"] = {"bytesBase64Encoded": match[2], "mimeType": match[1]}
    params: Dict[str, Any] = {}
    if aspect in ("16:9", "9:16"):
        params["aspectRatio"] = aspect
    if seconds:
        params["durationSeconds"] = int(seconds)
    name = model.split("/", 1)[1] if model.startswith("models/") else model
    try:
        resp = await session.post(f"{root}/v1beta/models/{urllib.parse.quote(name, safe='.-_')}:predictLongRunning",
                                  headers=headers, json={"instances": [instance], "parameters": params},
                                  timeout=_timeout())
    except Exception as exc:
        raise ProviderNetworkError(f"Network error during video generation: {type(exc).__name__}: {exc}")
    if resp.status_code != 200:
        await _raise(provider, resp)
    op = resp.json() or {}
    op_name = str(op.get("name") or "")
    if not op_name.startswith("operations/") and "/operations/" not in op_name:
        raise ProviderServerError("Veo accepted the request but returned no operation")
    started = time.time()
    while not op.get("done"):
        if time.time() - started > VIDEO_TIMEOUT_SECONDS:
            raise ProviderServerError("Video generation did not finish in 20 minutes")
        await asyncio.sleep(VIDEO_POLL_SECONDS * 2)
        try:
            poll = await session.get(f"{root}/v1beta/{op_name}", headers=headers, timeout=60)
        except Exception as exc:
            raise ProviderNetworkError(f"Network error while waiting for the video: {type(exc).__name__}")
        if poll.status_code != 200:
            await _raise(provider, poll)
        op = poll.json() or {}
        if on_status:
            try:
                on_status({"state": "running"})
            except Exception:
                pass
    if op.get("error"):
        err = op["error"]
        raise ProviderServerError("Veo: " + str(err.get("message") if isinstance(err, dict) else err)[:300])
    response = op.get("response") or {}
    samples = ((response.get("generateVideoResponse") or {}).get("generatedSamples")
               or response.get("generatedVideos") or [])
    uris = [((s or {}).get("video") or {}).get("uri") for s in samples]
    # The API key goes along with the download: only to Google itself (same origin as the API or *.googleapis.com).
    def _trusted(uri: str) -> bool:
        try:
            host = urllib.parse.urlparse(uri).hostname or ""
        except Exception:
            return False
        return uri.startswith(root + "/") or (uri.startswith("https://") and host.endswith(".googleapis.com"))
    uris = [u for u in uris if isinstance(u, str) and _trusted(u)]
    if not uris:
        filtered = (response.get("generateVideoResponse") or {}).get("raiMediaFilteredReasons")
        raise ProviderServerError("Veo returned no video" + (f": {str(filtered)[:240]}" if filtered else ""))
    raw, mime = await _download(session, uris[0], {"x-goog-api-key": key})
    return [_data_url(raw, mime)]


async def generate_video(provider: Any, account: Any, prompt: str, model: str, aspect: str = "16:9",
                         images: Optional[List[str]] = None, seconds: int = 0, on_status=None) -> List[str]:
    images = [str(x) for x in (images or []) if str(x).startswith("data:image")][:1]
    root = _google_root(provider.base_of(account))
    if root:
        return await _veo_video(provider, account, root, prompt, model, aspect, seconds, images, on_status)
    return await _openai_video(provider, account, prompt, model, aspect, seconds, images, on_status)


# --------------------------------------------------------------------------- catalogue


def _catalog_kind(entry: Any) -> str:
    """Round 33 (r33-models): what the provider's own model row says (``supported_endpoint_types``, output
    modalities) — a generator whose name gives nothing away (``wanx2.1-t2i-turbo``) is still found."""
    try:
        from . import model_capabilities
        summary = model_capabilities.capabilities_for(entry.id, entry=entry)
    except Exception:
        return ""
    kind = str(summary.get("kind") or "")
    if kind in ("image", "video"):
        return kind
    return ""


def custom_media_ids(enabled_models: List[Any], accounts: List[Any]) -> Tuple[List[str], List[str]]:
    """Public ids of Custom models that generate images / videos: declared in the connection or known by name."""
    images, videos = set(), set()
    declared_images, declared_videos = set(), set()
    for account in accounts:
        prefix = account_prefix(account)
        declared_images.update(f"{prefix}-{name}" for name in declared(account, "image_models"))
        declared_videos.update(f"{prefix}-{name}" for name in declared(account, "video_models"))
    out_images: List[str] = []
    out_videos: List[str] = []
    for entry in enabled_models:
        if getattr(entry, "provider", "") != "custom":
            continue
        raw = str(getattr(entry, "model", "") or "")
        kind = _catalog_kind(entry)
        if entry.id in declared_videos or (entry.id not in declared_images and (is_video_name(raw) or kind == "video")):
            if entry.id not in videos:
                videos.add(entry.id)
                out_videos.append(entry.id)
        elif entry.id in declared_images or is_image_name(raw) or kind == "image":
            if entry.id not in images:
                images.add(entry.id)
                out_images.append(entry.id)
    return out_images, out_videos
