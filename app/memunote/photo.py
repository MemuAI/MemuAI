"""Round 32 (photo): backend of the MemuNote photo editor — routes ``/api/photo/*`` added to the HTTP handler.

``register(Handler)`` (called once at the end of ``server.py``) attaches the ``api_photo_*`` methods, so the
dispatcher (``/api/photo/decode`` → ``api_photo_decode``) and the token / Host checks stay exactly the same.

Routes:
* ``GET  caps``       — codecs (Pillow, AVIF, HEIF) + AI state: vision models and image-generation models of the Proxy.
* ``POST decode``     — ``{path, frame}`` → ``{width, height, png(b64), frames, format, layers?}`` (opened files only).
* ``POST frames``     — ``{path}`` → every frame / icon size as PNG (GIF, APNG, WebP, TIFF, ICO).
* ``POST encode``     — raw PNG body, ``?path&format&quality&background&mtime&force&extra`` → written atomically.
* ``POST psd``        — ``{path, width, height, layers, merged, mtime, force}`` → layered PSD written atomically.
* ``POST ai``         — SSE vision chat: ``{action, image (data URL), question, history, edits, lang, model, width, height}``.
* ``POST generate``   — SSE image generation / AI edit through the Proxy (``/api/admin/pg/image``); results come back as
  ``data:`` URLs (the page CSP allows no remote images).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, List, Optional

log = logging.getLogger("memunote.photo")

PHOTO_EXT = {".tif", ".tiff", ".psd", ".psb", ".heic", ".heif", ".memuimg", ".jfif", ".pjpeg", ".pjp", ".tga",
             ".pcx", ".ppm", ".pgm", ".pbm", ".pnm", ".dds", ".jp2", ".j2k", ".apng", ".cur", ".xbm", ".sgi", ".icns"}
MAX_AI_IMAGE = 12 * 1024 * 1024
ACTIONS = ("describe", "ask", "alt", "caption", "ocr", "tags", "enhance", "command", "locate", "critique", "prompt", "palette_names")

OPS_SCHEMA = """Answer with ONE JSON object and nothing else (no code fence):
{"reply": "<one or two short sentences for the user, in the user's language>", "ops": [<operations>]}
Operations (applied in order; adjustments and filters affect the current selection if there is one):
{"op":"adjust","name":N,"params":{...}} where N and params are:
  brightnessContrast {brightness:-100..100, contrast:-100..100}; exposure {exposure:-3..3 (EV), gamma:0.3..3};
  levels {inBlack:0..255, inWhite:0..255, gamma:0.2..5}; hueSaturation {hue:-180..180, saturation:-100..100, lightness:-100..100, colorize:bool};
  vibrance {vibrance:-100..100, saturation:-100..100}; temperature {temperature:-100..100 (positive = warmer), tint:-100..100};
  colorBalance {cyanRed:-100..100, magentaGreen:-100..100, yellowBlue:-100..100}; curves {points:[[0,0],[x,y],...,[255,255]]};
  autoLevels {}; blackWhite {}; sepia {amount:0..100}; invert {}; threshold {level:0..255}; posterize {levels:2..32}
{"op":"filter","name":F,"params":{...}} where F is gaussianBlur {radius:1..60}, sharpen {amount:0..200}, unsharpMask {amount:0..300, radius:0.5..10},
  noise {amount:0..100}, denoise {radius:1..3}, pixelate {size:2..100}, vignette {amount:0..100, size:5..100}, emboss {}, edges {},
  motionBlur {angle:-180..180, distance:1..80}, oilPaint {radius:1..6}
{"op":"crop","box":[x0,y0,x1,y1]}            boxes are ALWAYS integers 0..1000 relative to the image width/height
{"op":"rotate","degrees":D}  {"op":"flip","axis":"horizontal"|"vertical"}  {"op":"resize","width":W,"height":H}
{"op":"select","box":[x0,y0,x1,y1],"target":"what"}   select an object (refined to its outline locally)
{"op":"select_all"} {"op":"deselect"} {"op":"invert_selection"}
{"op":"remove_background","box":[x0,y0,x1,y1]}      box = the subject to keep
{"op":"blur_background","box":[x0,y0,x1,y1],"radius":R}   box = the subject to keep sharp
{"op":"remove_object","box":[x0,y0,x1,y1]}          the object is erased and filled from its surroundings
{"op":"text","text":"...","box":[x0,y0,x1,y1],"color":"#rrggbb","font":"Segoe UI","bold":true}
{"op":"fill","color":"#rrggbb"}   fill the selection (or the layer)
Use only these operations. Prefer few, moderate, natural-looking values. If the request cannot be done with them, return "ops": [] and explain."""

SYSTEM = {
    "describe": "You are the vision assistant of MemuNote's photo editor. Describe the image the user is editing: subject, "
                "setting, composition, colours, light, style, any text. Then, if an edit history is given, add a short "
                "section about what the edits changed (the image you see is the CURRENT result). Use Markdown with short "
                "headings. Be concrete; never invent details you cannot see.",
    "ask": "You are the vision assistant of MemuNote's photo editor. The attached image is the CURRENT state of the document "
           "(all edits applied). Answer the user's questions about it precisely and briefly. If they ask how to change "
           "something, explain which editor tool or AI command does it. Never invent details you cannot see.",
    "alt": "Write alt text for this image for a screen reader: one sentence, at most 125 characters, no 'image of'. "
           "Reply with the alt text only.",
    "caption": "Write 3 different short captions for this image (descriptive, playful, social-media with 2-3 hashtags). "
               "Reply as a Markdown numbered list only.",
    "ocr": "Extract ALL text visible in the image exactly as written, preserving line breaks and reading order. Do not "
           "translate or correct. If there is no text, reply exactly NO_TEXT.",
    "tags": "List 10-15 search keywords for this image, comma separated, lower case. Reply with the keywords only.",
    "critique": "Give practical feedback on this photo as an experienced photo editor: composition, exposure, colour, "
                "sharpness, distractions; then 3-5 concrete edits to try in a photo editor. Markdown, concise.",
    "prompt": "Write a detailed prompt for an image-generation model that would recreate this image (subject, style, "
              "lighting, camera, colours). Reply with the prompt only, in English.",
    "palette_names": "Name the colours of this palette in a few words each. Reply as a Markdown list: `#hex` — name — where it appears.",
    "enhance": "You are an expert photo retoucher. Look at the image and propose a natural auto-enhancement using the "
               "editor's operations (exposure, levels, white balance/temperature, vibrance, sharpening, denoise...). "
               "Keep it subtle unless the image is clearly flawed.\n" + OPS_SCHEMA,
    "command": "You turn the user's natural-language edit request into operations of MemuNote's photo editor, looking at "
               "the attached image (the CURRENT state).\n" + OPS_SCHEMA,
    "locate": "Find the object(s) the user names in the image. Answer with ONE JSON object and nothing else: "
              "{\"objects\": [{\"label\": \"...\", \"box\": [x0, y0, x1, y1]}]} where the box is integers 0..1000 relative "
              "to the image width and height (x0,y0 = top-left). Tight boxes. If the object is not visible: {\"objects\": []}.",
}

def build_messages(body: Dict[str, Any], lang: str) -> List[Dict[str, Any]]:
    action = str(body.get("action") or "describe")
    if action not in ACTIONS:
        raise ValueError("unknown action")
    image = str(body.get("image") or "")
    if not image.startswith("data:image/") or len(image) > MAX_AI_IMAGE:
        raise ValueError("image (data URL) required")
    lang_line = "Answer in Russian." if lang == "ru" else "Answer in English."
    if action in ("prompt", "ocr", "tags"):
        lang_line = "" if action != "tags" else lang_line
    system = SYSTEM[action] + ("\n" + lang_line if lang_line else "")
    w, h = int(body.get("width") or 0), int(body.get("height") or 0)
    doc = f"Document: {body.get('name') or 'untitled'}, {int(body.get('doc_width') or w)}×{int(body.get('doc_height') or h)} px"
    if w and h:
        doc += f"; the attached preview is {w}×{h} px"
    edits = [str(e)[:200] for e in (body.get("edits") or []) if str(e).strip()][:80]
    layers = [str(x)[:120] for x in (body.get("layers") or [])][:40]
    context = doc + "."
    if layers:
        context += "\nLayers (bottom→top): " + "; ".join(layers)
    if edits:
        context += "\nEdit history (oldest→newest): " + "; ".join(edits)
    elif action in ("describe", "ask"):
        context += "\nEdit history: none (the image is as opened or generated)."
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system + "\n\n<editor_state>\n" + context + "\n</editor_state>"}]
    if action == "ask":
        for item in (body.get("history") or [])[-16:]:
            if isinstance(item, dict) and item.get("role") in ("user", "assistant") and str(item.get("text") or "").strip():
                messages.append({"role": item["role"], "content": str(item["text"])[:8000]})
    question = str(body.get("question") or "").strip()[:4000]
    if action in ("ask", "command", "locate") and not question:
        raise ValueError("question required")
    if action == "describe" and question:
        text = "Describe the image. Focus: " + question
    elif action == "palette_names":
        text = "Palette: " + ", ".join(str(c) for c in (body.get("palette") or [])[:12])
    else:
        text = question or {"describe": "Describe the image.", "alt": "Alt text, please.", "caption": "Captions, please.",
                            "ocr": "Extract the text.", "tags": "Keywords, please.", "enhance": "Auto-enhance this photo.",
                            "critique": "Critique this photo.", "prompt": "Write the prompt."}.get(action, "")
    messages.append({"role": "user", "content": [{"type": "text", "text": text},
                                                 {"type": "image_url", "image_url": {"url": image}}]})
    return messages

def parse_json_answer(text: str) -> Optional[Dict[str, Any]]:
    """The first JSON object in a model answer (code fences and prose around it are tolerated)."""
    s = re.sub(r"```(?:json)?", "", str(text or ""))
    start = s.find("{")
    while start >= 0:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(s[start:i + 1])
                        if isinstance(value, dict):
                            return value
                    except ValueError:
                        break
                    break
        start = s.find("{", start + 1)
    return None

def pick_models(status: Dict[str, Any], preferred: str = "") -> Dict[str, Any]:
    models = status.get("models") or []
    vision = [m for m in models if m.get("vision")]
    ids = [m["id"] for m in models]
    chosen = preferred if preferred in ids else ""
    if not chosen:
        default = status.get("default_model") or ""
        chosen = default if any(m["id"] == default and m.get("vision") for m in models) else (vision[0]["id"] if vision else default)
    return {"model": chosen, "vision": [m["id"] for m in vision],
            "chosen_vision": any(m["id"] == chosen and m.get("vision") for m in models) or not any("vision" in m for m in models)}

def image_models(base: str) -> List[Dict[str, Any]]:
    from . import ai
    try:
        data = ai.request_json(base, "GET", "/api/admin/pg/state", timeout=15)
    except ai.AIError:
        return []
    rows = []
    labels = {str(m.get("id")): m for m in (data or {}).get("models") or [] if isinstance(m, dict)}
    for mid in (data or {}).get("image_models") or []:
        row = labels.get(str(mid), {})
        if str(mid) == "genspark-image/auto" and len((data or {}).get("image_models") or []) > 1:
            pass
        rows.append({"id": str(mid), "label": str(row.get("name") or row.get("label") or ai._pretty(str(mid))),
                     "provider": str(row.get("provider") or "")})
    return rows

def _fetch_data_url(url: str) -> str:
    if url.startswith("data:image/"):
        return url
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("https", "http"):
        raise ValueError("bad image url")
    req = urllib.request.Request(url, headers={"User-Agent": "MemuNote"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - result URL given by the user's own Proxy
        raw = resp.read(40 * 1024 * 1024 + 1)
        ctype = (resp.headers.get("Content-Type") or "image/png").split(";")[0]
    if len(raw) > 40 * 1024 * 1024:
        raise ValueError("image too large")
    if not ctype.startswith("image/"):
        ctype = "image/png"
    return f"data:{ctype};base64," + base64.b64encode(raw).decode("ascii")

def _allowed_path(handler, value: Any) -> str:
    from .server import _clean_path
    path = _clean_path(value)
    if not handler.app.allowed(path):
        raise PermissionError("not opened")
    return path

def api_photo_caps(self, method: str) -> None:
    from . import ai
    from . import settings as settings_store
    from .formats import images
    settings = settings_store.load()
    status = ai.status(settings.get("proxy_url", ""), force=self._query().get("force") == "1")
    picked = pick_models(status, str(settings.get("photo_model") or settings.get("ai_model") or ""))
    gen = image_models(ai.discover(settings.get("proxy_url", ""))) if status.get("online") else []
    self._json({"ok": True, "codecs": images.capabilities(), "online": bool(status.get("online")),
                "ai_enabled": bool(settings.get("ai_enabled", True)), "model": picked["model"],
                "vision_models": picked["vision"], "model_has_vision": picked["chosen_vision"],
                "models": [{"id": m["id"], "label": m.get("label") or m["id"], "vision": bool(m.get("vision"))}
                           for m in status.get("models") or []],
                "image_models": gen})

def api_photo_decode(self, method: str) -> None:
    from .formats import images
    body = self._json_body()
    path = _allowed_path(self, body.get("path"))
    try:
        result = images.decode(path, int(body.get("frame") or 0))
    except images.ImageFormatError as exc:
        self._error(str(exc), 415, code=exc.code)
        return
    except ImportError:
        self._error("Pillow is not available", 501, code="no_pillow")
        return
    self._json({"ok": True, **result})

def api_photo_frames(self, method: str) -> None:
    from .formats import images
    body = self._json_body()
    path = _allowed_path(self, body.get("path"))
    self._json({"ok": True, **images.frames(path, int(body.get("limit") or images.MAX_FRAMES))})

def _write(self, path: str, data: bytes, query: Dict[str, str]) -> None:
    from .formats.textio import atomic_write
    from .server import _stat
    current = _stat(path)
    expected = int(query.get("mtime") or 0)
    if query.get("force") != "1" and expected and current["exists"] and abs(current["mtime"] - expected) > 2:
        self._json({"ok": False, "conflict": True, **current})
        return
    atomic_write(path, data, keep_backup=query.get("backup") == "1")
    self.app.allow(path)
    self._json({"ok": True, **_stat(path)})

def api_photo_encode(self, method: str) -> None:
    from .formats import images
    from .server import MAX_TEXT, _clean_path
    query = self._query()
    path = _clean_path(query.get("path", ""))
    raw = self._body(MAX_TEXT)
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        self._error("PNG body expected", 400)
        return
    try:
        extra = json.loads(query.get("extra") or "{}")
        data = images.encode(raw, query.get("format", "png"), int(query.get("quality") or 90),
                             query.get("background", "#ffffff"), extra if isinstance(extra, dict) else {})
    except images.ImageFormatError as exc:
        self._error(str(exc), 415, code=exc.code)
        return
    if query.get("return") == "1":
        self._send(200, data, "application/octet-stream")
        return
    _write(self, path, data, query)

def api_photo_psd(self, method: str) -> None:
    from .formats import psd
    from .server import _clean_path
    body = self._json_body()
    path = _clean_path(body.get("path", ""))

    def unb64(items):
        out = []
        for layer in items or []:
            item = dict(layer)
            if isinstance(item.get("png"), str):
                item["png"] = base64.b64decode(item["png"].split(",")[-1])
            if isinstance(item.get("mask"), dict) and isinstance(item["mask"].get("png"), str):
                item["mask"] = {**item["mask"], "png": base64.b64decode(item["mask"]["png"].split(",")[-1])}
            if item.get("children"):
                item["children"] = unb64(item["children"])
            out.append(item)
        return out

    merged = body.get("merged")
    data = psd.write_psd(int(body.get("width") or 0), int(body.get("height") or 0), unb64(body.get("layers")),
                         base64.b64decode(merged.split(",")[-1]) if isinstance(merged, str) and merged else None)
    _write(self, path, data, {"mtime": str(body.get("mtime") or 0), "force": "1" if body.get("force") else ""})

def _sse_start(self) -> None:
    self.send_response(200)
    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
    self.send_header("Cache-Control", "no-store")
    self.send_header("X-Accel-Buffering", "no")
    self.end_headers()

def api_photo_ai(self, method: str) -> None:
    from . import ai
    from . import settings as settings_store
    body = self._json_body()
    settings = settings_store.load()
    if not settings.get("ai_enabled", True):
        self._error("ai_disabled", 403)
        return
    messages = build_messages(body, body.get("lang") if body.get("lang") in ("ru", "en") else self.app.lang())
    status = ai.status(settings.get("proxy_url", ""))
    if not status.get("online"):
        self._error("offline", 503, kind="offline")
        return
    picked = pick_models(status, str(body.get("model") or settings.get("photo_model") or settings.get("ai_model") or ""))
    model = picked["model"]
    if not model:
        self._error("no_model", 503)
        return
    request_id = str(body.get("id") or uuid.uuid4().hex)[:64]
    cancel = threading.Event()
    state: Dict[str, Any] = {"cancel": cancel, "conn": None}
    self.app.streams[request_id] = state
    _sse_start(self)

    def emit(event: Dict[str, Any]) -> bool:
        try:
            self.wfile.write(b"data: " + json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n\n")
            self.wfile.flush()
            return True
        except OSError:
            cancel.set()
            return False

    emit({"t": "start", "id": request_id, "model": model, "vision": picked["chosen_vision"]})
    parts: List[str] = []
    action = str(body.get("action"))
    structured = action in ("command", "enhance", "locate")
    try:
        payload = {"model": model, "messages": messages, "web_search": False, "thinking": False,
                   "temperature": 0.2 if structured else None, "max_tokens": 4096 if not structured else 2048}
        for event in ai.stream_chat(ai.discover(settings.get("proxy_url", "")), payload, cancel,
                                    lambda c: state.__setitem__("conn", c)):
            if event["t"] == "delta":
                parts.append(event["text"])
                if not structured and not emit(event):
                    break
            elif event["t"] in ("usage", "finish"):
                emit(event)
        if structured and not cancel.is_set():
            text = "".join(parts)
            parsed = parse_json_answer(text)
            if parsed is None:
                emit({"t": "result", "ok": False, "raw": text[:4000]})
            else:
                emit({"t": "result", "ok": True, "data": parsed})
        emit({"t": "done", "cancelled": cancel.is_set()})
    except ai.AIError as exc:
        emit({"t": "error", "message": str(exc), "kind": exc.kind, "status": exc.status})
    except Exception as exc:  # noqa: BLE001
        if not cancel.is_set():
            log.exception("photo ai failed")
            emit({"t": "error", "message": f"{type(exc).__name__}: {exc}"[:400], "kind": "internal"})
    finally:
        self.app.streams.pop(request_id, None)
        self.close_connection = True

def api_photo_generate(self, method: str) -> None:
    from . import ai
    from . import settings as settings_store
    body = self._json_body()
    settings = settings_store.load()
    prompt = str(body.get("prompt") or "").strip()[:8000]
    model = str(body.get("model") or "")
    if not prompt:
        self._error("empty prompt")
        return
    base = ai.discover(settings.get("proxy_url", ""))
    if not ai.health(base):
        self._error("offline", 503, kind="offline")
        return
    if not model:
        rows = image_models(base)
        model = rows[0]["id"] if rows else ""
    if not model:
        self._error("no_image_model", 503, kind="no_image_model")
        return
    images = [str(x) for x in (body.get("images") or []) if str(x).startswith("data:image/")][:4]
    _sse_start(self)

    def emit(event: Dict[str, Any]) -> bool:
        try:
            self.wfile.write(b"data: " + json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n\n")
            self.wfile.flush()
            return True
        except OSError:
            return False

    emit({"t": "start", "model": model})
    conn = ai._conn(base, 900)
    try:
        payload = {"prompt": prompt, "model": model, "aspect": str(body.get("aspect") or "auto"), "images": images}
        conn.request("POST", "/api/admin/pg/image", body=json.dumps(payload).encode("utf-8"),
                     headers=ai._headers({"Accept": "text/event-stream"}))
        response = conn.getresponse()
        if response.status != 200:
            raw = response.read()[:2000].decode("utf-8", "replace")
            try:
                parsed = json.loads(raw)
                raw = parsed.get("error") if isinstance(parsed, dict) else raw
                if isinstance(raw, dict):
                    raw = raw.get("message") or json.dumps(raw, ensure_ascii=False)
            except ValueError:
                pass
            emit({"t": "error", "message": str(raw)[:600], "status": response.status})
            return
        for line in ai._sse_lines(response):
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw == "[DONE]":
                break
            try:
                item = json.loads(raw)
            except ValueError:
                continue
            status = item.get("status")
            if status == "done":
                urls = []
                for url in item.get("urls") or []:
                    try:
                        urls.append(_fetch_data_url(str(url)))
                    except Exception as exc:  # noqa: BLE001
                        log.warning("generated image download failed: %s", exc)
                emit({"t": "done", "images": urls, "ms": item.get("ms")})
            elif status == "error":
                emit({"t": "error", "message": str(item.get("error") or "error")[:600]})
            elif not emit({"t": "status", "status": status, "ms": item.get("ms")}):
                break
    except (OSError, ValueError) as exc:
        emit({"t": "error", "message": f"{type(exc).__name__}: {exc}"[:400], "kind": "offline"})
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        self.close_connection = True

def register(handler_cls) -> None:
    for name, fn in list(globals().items()):
        if name.startswith("api_photo_") and callable(fn):
            setattr(handler_cls, name, fn)
