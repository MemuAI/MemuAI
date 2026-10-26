"""Round 33 (r33-theme, item #28): the theme & appearance editor — data model and routes.

The editor itself is a mode of the main window (``pgstatic/theme-editor.js``). Everything it changes is either an
existing appearance preference (theme, accent, design, corners, glass, font …) or lives in ONE new preference,
``ui_look``, normalised here::

    ui_look = {
      "v": 1,
      "layout":   {side_pos, side_mode, side_width, topbar, composer, composer_width, chat_px, bubbles, user_align},
      "toolbar":  {"order": [item ids], "hidden": [item ids]},
      "elements": {element_id: {"all": {prop: value}, "dark": {prop: value}, "light": {prop: value}}},
      "tokens":   {"dark": {"--token": value}, "light": {...}},
      "bg":       {kind, preset, c1, c2, angle, image, dim, blur, canvas},
    }

The page turns it into attributes on <html> plus one generated stylesheet (``<style id="memu-look">``), so nothing is
patched per DOM node. Colours are kept per light/dark mode; shapes, sizes and layout are shared by both.

Custom themes (``<MEMU_DIR>/themes/*.css``, :mod:`app.themes`) stay plain CSS files that work without the editor.
A theme saved by the editor additionally carries a versioned, machine-readable package in a trailing comment::

    /* @memu-editor {"schema": 1, "tokens": {...}, "elements": {...}, "look": {...}} */

``package_from_css`` reads it back (and migrates a schema-0 file — any older theme — from its ``:root`` tokens).
Export/import: a ``.memutheme.json`` file (``{"format": "memuai-theme", "schema": 1, ...}``) or a short share code
``MEMU-THEME-1.<base64url(zlib(json))>``.

Routes (``register(app)``):
    POST /api/pg/theme-editor/parse     {text}               → {package}   (css theme / json / share code)
    POST /api/pg/theme-editor/share     {package}            → {code, dropped_image}
    POST /api/pg/theme-editor/theme     {id?, name, base, extends, author, description, css, package} → {theme}
    POST /api/pg/theme-editor/asset     {data: data-URL}     → {url}        (background images)
    GET  /api/pg/theme-editor/asset/{name}
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Request

SCHEMA = 1
LOOK_VERSION = 1
SHARE_PREFIX = "MEMU-THEME-1."
EDITOR_BLOCK_RE = re.compile(r"/\*\s*@memu-editor\s+(\{.*?\})\s*\*/", re.S)
ELEMENTS_START = "/* memu-editor:elements */"
ELEMENTS_END = "/* /memu-editor:elements */"

LAYOUT_CHOICES: Dict[str, tuple] = {
    "side_pos": ("left", "right"),
    "side_mode": ("full", "compact"),
    "topbar": ("", "slim", "bare", "card"),
    "composer": ("", "float", "docked", "minimal"),
    "bubbles": ("", "plain", "outline", "filled", "compact"),
    "user_align": ("", "left"),
}

LAYOUT_RANGES: Dict[str, tuple] = {
    "side_width": (200, 480),
    "composer_width": (420, 1400),
    "chat_px": (520, 1600),
}

TOOLBAR_ITEMS = ("nav", "model", "context", "modules", "spacer", "panels", "help", "focus", "theme")

HIDEABLE = ("nav", "search", "model", "context", "modules", "panels", "help", "focus", "theme",
            "mic", "live", "hint", "brand", "modes", "sched", "skills", "status")

PROP_KINDS: Dict[str, Any] = {
    "bg": "color", "fg": "color", "border": "color", "accent": "color",
    "alpha": (0, 100), "bw": (0, 8), "radius": (0, 48), "blur": (0, 40), "size": (9, 40), "pad": (0, 48),
    "opacity": (20, 100),
    "shadow": ("none", "soft", "medium", "strong", "glow"),
    "font": ("sans", "serif", "mono", "system", "rounded"),
    "weight": ("400", "500", "600", "700", "800"),
}
COLOR_PROPS = {key for key, kind in PROP_KINDS.items() if kind == "color"}
ELEMENT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
TOKEN_RE = re.compile(r"^--[a-z][a-z0-9-]{0,47}$")
MAX_ELEMENTS = 80
MAX_TOKENS = 96

BG_KINDS = ("", "gradient", "image", "solid")
BG_PRESETS = ("aurora", "sunset", "ocean", "forest", "dusk", "mono", "accent", "custom")
ASSET_RE = re.compile(r"^[a-f0-9]{20}\.(png|jpg|webp)$")
ASSET_URL_RE = re.compile(r"^/api/pg/theme-editor/asset/[a-f0-9]{20}\.(png|jpg|webp)$")
MAX_ASSET_BYTES = 6 * 1024 * 1024

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_FUNC_RE = re.compile(r"^[a-zA-Z0-9#%().,\-+*/ ]+$")
_ALLOWED_FUNCS = {"rgb", "rgba", "hsl", "hsla", "color-mix", "var", "in", "srgb", "oklch", "oklab", "lab", "lch",
                  "hwb", "transparent", "currentcolor", "white", "black", "calc", "linear-gradient",
                  "radial-gradient", "conic-gradient", "deg", "turn", "to", "left", "right", "top", "bottom",
                  "circle", "ellipse", "at", "center", "closest-side", "farthest-corner", "px", "em", "rem"}

def safe_value(value: Any, *, max_len: int = 240) -> str:
    """A CSS value that cannot break out of its declaration: colours, var(), color-mix(), gradients, numbers.

    Returns "" for anything else (``;``, braces, quotes, ``url(``, comments, ``expression`` …).
    """
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    text = str(value).strip()
    if not text or len(text) > max_len:
        return ""
    if _COLOR_RE.match(text):
        return text
    if not _FUNC_RE.match(text) or "/*" in text or "*/" in text or text.count("(") != text.count(")"):
        return ""
    for word in re.findall(r"[a-zA-Z][a-zA-Z-]*", text):
        low = word.lower()
        if low.startswith("--") or low in _ALLOWED_FUNCS:
            continue

        if re.search(re.escape(word) + r"\s*\(", text) and low not in _ALLOWED_FUNCS:
            return ""
    return text

def safe_color(value: Any) -> str:
    text = safe_value(value, max_len=160)
    return "" if "gradient" in text else text

def _choice(value: Any, choices: tuple) -> str:
    return value if isinstance(value, str) and value in choices else choices[0]

def _ranged(value: Any, lo: int, hi: int, *, zero_ok: bool = True) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    if number == 0 and zero_ok:
        return 0
    return max(lo, min(hi, number))

def _props(raw: Any, *, colors: bool) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        kind = PROP_KINDS.get(key)
        if kind is None or (key in COLOR_PROPS) != colors:
            continue
        if kind == "color":
            clean = safe_color(value)
            if clean:
                out[key] = clean
        elif isinstance(kind, tuple) and kind and isinstance(kind[0], int):
            if value is None or value == "":
                continue
            try:
                out[key] = max(kind[0], min(kind[1], int(round(float(value)))))
            except (TypeError, ValueError):
                continue
        elif isinstance(kind, tuple) and isinstance(value, str) and value in kind:
            out[key] = value
    return out

def normalize_elements(raw: Any) -> Dict[str, Dict[str, Dict[str, Any]]]:
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    if not isinstance(raw, dict):
        return out
    for element_id, spec in list(raw.items())[:MAX_ELEMENTS * 2]:
        if not isinstance(element_id, str) or not ELEMENT_ID_RE.match(element_id) or not isinstance(spec, dict):
            continue
        entry = {"all": _props(spec.get("all"), colors=False),
                 "dark": _props(spec.get("dark"), colors=True),
                 "light": _props(spec.get("light"), colors=True)}
        entry = {key: value for key, value in entry.items() if value}
        if entry:
            out[element_id] = entry
        if len(out) >= MAX_ELEMENTS:
            break
    return out

def normalize_tokens(raw: Any) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {"dark": {}, "light": {}}
    if not isinstance(raw, dict):
        return out
    for mode in ("dark", "light"):
        block = raw.get(mode)
        if not isinstance(block, dict):
            continue
        for name, value in list(block.items())[:MAX_TOKENS]:
            clean = safe_value(value)
            if isinstance(name, str) and TOKEN_RE.match(name) and clean:
                out[mode][name] = clean
    return out

def normalize_layout(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    out: Dict[str, Any] = {key: _choice(raw.get(key), choices) for key, choices in LAYOUT_CHOICES.items()}
    for key, (lo, hi) in LAYOUT_RANGES.items():
        out[key] = _ranged(raw.get(key), lo, hi)
    return out

def normalize_toolbar(raw: Any) -> Dict[str, List[str]]:
    raw = raw if isinstance(raw, dict) else {}
    order: List[str] = []
    for item in raw.get("order") or []:
        if isinstance(item, str) and item in TOOLBAR_ITEMS and item not in order:
            order.append(item)
    if order:
        order += [item for item in TOOLBAR_ITEMS if item not in order]
    hidden = sorted({item for item in raw.get("hidden") or [] if isinstance(item, str) and item in HIDEABLE},
                    key=HIDEABLE.index)
    return {"order": order, "hidden": hidden}

def normalize_bg(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    kind = _choice(raw.get("kind"), BG_KINDS)
    image = str(raw.get("image") or "")
    out: Dict[str, Any] = {
        "kind": kind,
        "preset": _choice(raw.get("preset"), BG_PRESETS),
        "c1": safe_color(raw.get("c1")),
        "c2": safe_color(raw.get("c2")),
        "angle": _ranged(raw.get("angle"), 0, 360, zero_ok=True),
        "image": image if ASSET_URL_RE.match(image) else "",
        "dim": _ranged(raw.get("dim"), 0, 85),
        "blur": _ranged(raw.get("blur"), 0, 40),
        "canvas": _ranged(raw.get("canvas"), 0, 70),
    }
    if kind == "image" and not out["image"]:
        out["kind"] = ""
    return out

def default_look() -> Dict[str, Any]:
    return normalize_look({})

def normalize_look(raw: Any) -> Dict[str, Any]:
    """The ``ui_look`` preference: always a complete, safe structure."""
    raw = raw if isinstance(raw, dict) else {}
    return {
        "v": LOOK_VERSION,
        "layout": normalize_layout(raw.get("layout")),
        "toolbar": normalize_toolbar(raw.get("toolbar")),
        "elements": normalize_elements(raw.get("elements")),
        "tokens": normalize_tokens(raw.get("tokens")),
        "bg": normalize_bg(raw.get("bg")),
    }

DEFAULTS: Dict[str, Any] = {"ui_look": default_look()}

def normalize_prefs(prefs: Dict[str, Any]) -> Dict[str, Any]:
    """In place, for ``pgdata._normalize_preferences`` (through ``appearance_extra.normalize``)."""
    prefs["ui_look"] = normalize_look(prefs.get("ui_look"))
    return prefs

LOOK_PREF_KEYS = ("ui_design", "accent_color", "app_font", "font_scale", "ui_density", "chat_width", "ui_radius",
                  "ui_glass", "ui_msg_size", "ui_contrast")

def _clean_look_prefs(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    from . import appearance_extra, themes
    out: Dict[str, Any] = {}
    for key in LOOK_PREF_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if key == "ui_design":
            out[key] = themes.normalize_design(value)
        elif key == "accent_color":
            out[key] = themes.normalize_accent(value)
        elif key == "app_font":
            out[key] = value if value in ("serif", "sans", "mono", "system") else "serif"
        elif key == "font_scale":
            out[key] = _ranged(value, 85, 125, zero_ok=False) or 100
        elif key == "ui_density":
            out[key] = value if value in ("compact", "", "spacious") else ""
        elif key == "chat_width":
            out[key] = "wide" if value == "wide" else "comfortable"
        elif key in appearance_extra.CHOICES:
            out[key] = value if value in appearance_extra.CHOICES[key] else appearance_extra.CHOICES[key][0]
    return out

def normalize_package(raw: Any) -> Dict[str, Any]:
    """A theme package of the current schema. Older shapes are migrated:

    * schema 0 / the legacy JSON export of the theme studio: ``{css, name, base, extends, author, description}``;
    * schema 1: ``{format, schema, name, base, extends, author, description, tokens, elements, css, look, prefs}``.
    """
    raw = raw if isinstance(raw, dict) else {}
    from . import themes
    schema = raw.get("schema")
    try:
        schema = int(schema)
    except (TypeError, ValueError):
        schema = 0
    css = str(raw.get("css") or "")
    tokens = raw.get("tokens") if isinstance(raw.get("tokens"), dict) else {}
    meta = themes.parse_meta(css) if css else {}
    if schema < 1:

        if css and not tokens:
            tokens = themes.extract_tokens(css)
    base = themes.normalize_mode(raw.get("base") or meta.get("base") or "dark")
    extends = str(raw.get("extends") if raw.get("extends") is not None else meta.get("extends") or "")
    if not themes.variant(base, extends):
        extends = ""
    clean_tokens = {name: safe_value(value) for name, value in list(tokens.items())[:MAX_TOKENS]
                    if isinstance(name, str) and TOKEN_RE.match(name) and safe_value(value)}
    elements_raw = raw.get("elements") if isinstance(raw.get("elements"), dict) else {}

    elements = {}
    for element_id, spec in normalize_elements(elements_raw).items():
        keep = {key: value for key, value in spec.items() if key in ("all", base)}
        if keep:
            elements[element_id] = keep
    rest = custom_css_of(css)
    out = {
        "format": "memuai-theme",
        "schema": SCHEMA,
        "name": str(raw.get("name") or meta.get("name") or "").strip()[:80],
        "base": base,
        "extends": extends,
        "author": str(raw.get("author") or meta.get("author") or "")[:80],
        "description": str(raw.get("description") or meta.get("description") or "")[:200],
        "tokens": clean_tokens,
        "elements": elements,
        "css": rest[:120_000],
        "look": None,
        "prefs": _clean_look_prefs(raw.get("prefs")),
    }
    if isinstance(raw.get("look"), dict):
        look = normalize_look(raw["look"])
        look["elements"], look["tokens"] = {}, {"dark": {}, "light": {}}
        out["look"] = look
    image_data = raw.get("image_data")
    if isinstance(image_data, str) and image_data.startswith("data:image/"):
        out["image_data"] = image_data[: MAX_ASSET_BYTES * 2]
    return out

def custom_css_of(css: str) -> str:
    """The hand-written part of a theme: no header, no :root token block, no generated element rules, no package."""
    from . import themes
    body = themes.strip_meta(css or "")
    body = EDITOR_BLOCK_RE.sub("", body)
    start, end = body.find(ELEMENTS_START), body.find(ELEMENTS_END)
    if start >= 0 and end > start:
        body = body[:start] + body[end + len(ELEMENTS_END):]
    body = re.sub(r"(^|\n)\s*:root\s*\{[^{}]*\}\s*", "\n", body, count=1)
    return body.strip()

def package_from_css(css: str) -> Dict[str, Any]:
    """Read the editor package of a theme file; any theme without one is migrated from schema 0."""
    from . import themes
    match = EDITOR_BLOCK_RE.search(css or "")
    meta = themes.parse_meta(css or "")
    raw: Dict[str, Any] = {}
    if match:
        try:
            raw = json.loads(match.group(1))
        except ValueError:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw = dict(raw)
    raw.setdefault("schema", 1 if match else 0)
    raw["css"] = css
    for key in ("name", "base", "extends", "author", "description"):
        if meta.get(key) and not raw.get(key):
            raw[key] = meta[key]
    if raw["schema"] >= 1:

        tokens = dict(raw.get("tokens") or {})
        tokens.update(themes.extract_tokens(css))
        raw["tokens"] = tokens
    return normalize_package(raw)

def editor_block(package: Dict[str, Any]) -> str:
    """The trailing comment that makes a theme file re-editable; ``*/`` can never appear inside."""
    data = {key: package.get(key) for key in ("schema", "tokens", "elements", "look", "prefs")}
    data["schema"] = SCHEMA
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("/", "\\/")
    return f"/* @memu-editor {text} */\n"

def share_code(package: Dict[str, Any]) -> Dict[str, Any]:
    clean = normalize_package(package)
    dropped = bool(clean.pop("image_data", None)) or bool(clean.get("look") and clean["look"]["bg"].get("image"))
    if clean.get("look") and clean["look"]["bg"].get("image"):
        clean["look"]["bg"]["image"] = ""
        if clean["look"]["bg"]["kind"] == "image":
            clean["look"]["bg"]["kind"] = ""
    raw = json.dumps(clean, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    code = SHARE_PREFIX + base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")
    return {"code": code, "dropped_image": dropped}

def parse_text(text: str) -> Dict[str, Any]:
    """Anything a user may paste or import: a share code, a JSON export (any schema) or a .css theme."""
    text = str(text or "").strip()
    if not text:
        raise ValueError("Пусто: вставьте код темы или выберите файл")
    if len(text) > 4 * 1024 * 1024:
        raise ValueError("Слишком большой файл темы")
    compact = re.sub(r"\s+", "", text)
    if compact.upper().startswith(SHARE_PREFIX.upper()):
        payload = compact[len(SHARE_PREFIX):]
        try:
            raw = zlib.decompress(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)), bufsize=65536)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise ValueError("Код темы повреждён или обрезан") from None
        return normalize_package(data)
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            raise ValueError("Не удалось прочитать JSON темы") from None
        if not isinstance(data, dict):
            raise ValueError("Не удалось прочитать JSON темы")
        if data.get("format") not in (None, "memuai-theme"):
            raise ValueError("Это не тема MemuAI")
        return normalize_package(data)
    if "{" in text or "@name" in text:
        return package_from_css(text)
    raise ValueError("Не похоже на тему MemuAI: ожидается код MEMU-THEME-1…, файл .json или .css")

def save_theme(body: Dict[str, Any]) -> Dict[str, Any]:
    """Create or replace a custom theme from the editor. ``css`` is the CSS the page generated (tokens + element
    rules + hand-written CSS); the package is normalised and appended as the re-editable block."""
    from . import themes
    if not isinstance(body, dict):
        raise ValueError("Ожидается объект темы")
    package = normalize_package({**(body.get("package") or {}), "name": body.get("name"), "base": body.get("base"),
                                 "extends": body.get("extends"), "schema": SCHEMA})
    css = str(body.get("css") or "")
    css = EDITOR_BLOCK_RE.sub("", themes.strip_meta(css)).rstrip()
    if "</style" in css.lower():
        raise ValueError("В теме не может быть </style>")
    package.pop("image_data", None)
    text = (css + "\n\n" if css else "") + editor_block(package)
    return themes.save_custom({
        "id": body.get("id") or None, "name": package["name"] or body.get("name") or "",
        "base": package["base"], "extends": package["extends"], "css": text,
        "author": body.get("author"), "description": body.get("description"),
        "version": body.get("version"),
    })

_MAGIC = {"png": b"\x89PNG\r\n\x1a\n", "jpg": b"\xff\xd8\xff", "webp": b"RIFF"}

def assets_dir() -> Path:
    from . import themes
    return themes.themes_dir() / "assets"

def store_asset(data_url: str) -> str:
    match = re.match(r"^data:image/(png|jpeg|jpg|webp);base64,([A-Za-z0-9+/=\s]+)$", str(data_url or ""))
    if not match:
        raise ValueError("Нужна картинка PNG, JPEG или WebP")
    ext = {"jpeg": "jpg"}.get(match.group(1), match.group(1))
    try:
        raw = base64.b64decode(match.group(2), validate=False)
    except Exception:
        raise ValueError("Картинка повреждена") from None
    if not raw or len(raw) > MAX_ASSET_BYTES:
        raise ValueError("Картинка больше 6 МБ")
    if not raw.startswith(_MAGIC[ext]) or (ext == "webp" and raw[8:12] != b"WEBP"):
        raise ValueError("Файл не похож на картинку этого формата")
    name = hashlib.sha256(raw).hexdigest()[:20] + "." + ext
    folder = assets_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if not path.exists():
        tmp = folder / (name + ".tmp")
        tmp.write_bytes(raw)
        tmp.replace(path)
    return "/api/pg/theme-editor/asset/" + name

def asset_path(name: str) -> Optional[Path]:
    if not isinstance(name, str) or not ASSET_RE.match(name):
        return None
    path = assets_dir() / name
    return path if path.is_file() else None

def asset_data_url(url: str) -> str:
    """For the export file: the background image travels inside it."""
    match = ASSET_URL_RE.match(str(url or ""))
    if not match:
        return ""
    path = asset_path(url.rsplit("/", 1)[-1])
    if not path:
        return ""
    mime = {"jpg": "jpeg"}.get(match.group(1), match.group(1))
    return f"data:image/{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")

ERRORS_EN = {
    "Пусто: вставьте код темы или выберите файл": "Empty: paste a theme code or choose a file",
    "Слишком большой файл темы": "The theme file is too large",
    "Код темы повреждён или обрезан": "The theme code is damaged or cut short",
    "Не удалось прочитать JSON темы": "Could not read the theme JSON",
    "Это не тема MemuAI": "This is not a MemuAI theme",
    "Не похоже на тему MemuAI: ожидается код MEMU-THEME-1…, файл .json или .css":
        "Does not look like a MemuAI theme: expected a MEMU-THEME-1… code, a .json or a .css file",
    "Ожидается объект темы": "A theme object is expected",
    "Ожидается объект": "An object is expected",
    "В теме не может быть </style>": "A theme cannot contain </style>",
    "Нужна картинка PNG, JPEG или WebP": "A PNG, JPEG or WebP image is needed",
    "Картинка повреждена": "The image is damaged",
    "Картинка больше 6 МБ": "The image is larger than 6 MB",
    "Файл не похож на картинку этого формата": "The file does not look like an image of this format",
    "Тема не найдена": "Theme not found",
}

def error_body(message: str) -> Dict[str, str]:
    """``{"error": RU, "error_en": EN}`` — the editor shows the one matching the UI language."""
    message = str(message or "")
    english = ERRORS_EN.get(message)
    if english is None:
        for prefix, prefix_en in (("Не удалось сохранить тему: ", "Could not save the theme: "),
                                  ("Не удалось сохранить картинку: ", "Could not save the image: ")):
            if message.startswith(prefix):
                english = prefix_en + message[len(prefix):]
                break
    if english is None:

        english = message if not re.search(r"[А-Яа-яЁё]", message) else "The theme could not be processed"
    return {"error": message, "error_en": english}

def register(app) -> None:
    import asyncio
    from fastapi.responses import FileResponse, JSONResponse

    async def _json(request: Request) -> Dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            body = None
        if not isinstance(body, dict):
            raise ValueError("Ожидается объект")
        return body

    @app.post("/api/pg/theme-editor/parse")
    async def theme_editor_parse(request: Request):
        try:
            body = await _json(request)
            package = await asyncio.to_thread(parse_text, body.get("text"))
            if package.get("image_data") and package.get("look"):
                try:
                    url = await asyncio.to_thread(store_asset, package["image_data"])
                    package["look"]["bg"]["image"] = url
                    package["look"]["bg"]["kind"] = "image"
                except ValueError:
                    pass
            package.pop("image_data", None)
            return {"ok": True, "package": package}
        except ValueError as exc:
            return JSONResponse(error_body(str(exc)), 400)

    @app.post("/api/pg/theme-editor/share")
    async def theme_editor_share(request: Request):
        try:
            body = await _json(request)
            return {"ok": True, **share_code(body.get("package") or {})}
        except ValueError as exc:
            return JSONResponse(error_body(str(exc)), 400)

    @app.post("/api/pg/theme-editor/export")
    async def theme_editor_export(request: Request):
        """The export file: the normalised package with the background image inlined."""
        try:
            body = await _json(request)
            package = normalize_package(body.get("package") or {})
            image = (package.get("look") or {}).get("bg", {}).get("image") if package.get("look") else ""
            if image:
                data = await asyncio.to_thread(asset_data_url, image)
                if data:
                    package["image_data"] = data
            return {"ok": True, "package": package}
        except ValueError as exc:
            return JSONResponse(error_body(str(exc)), 400)

    @app.post("/api/pg/theme-editor/theme")
    async def theme_editor_save_theme(request: Request):
        try:
            body = await _json(request)
            saved = await asyncio.to_thread(save_theme, body)
            saved["package"] = package_from_css(saved.get("css") or "")
            return {"ok": True, "theme": saved}
        except ValueError as exc:
            return JSONResponse(error_body(str(exc)), 400)
        except OSError as exc:
            return JSONResponse(error_body(f"Не удалось сохранить тему: {exc}"), 500)

    @app.post("/api/pg/theme-editor/package")
    async def theme_editor_package(request: Request):
        """The editor package of an existing custom theme (any schema)."""
        from . import themes
        try:
            body = await _json(request)
            theme = await asyncio.to_thread(themes.get_custom, str(body.get("id") or ""))
            if not theme:
                return JSONResponse(error_body("Тема не найдена"), 404)
            return {"ok": True, "package": package_from_css(theme["css"])}
        except ValueError as exc:
            return JSONResponse(error_body(str(exc)), 400)

    @app.post("/api/pg/theme-editor/asset")
    async def theme_editor_asset_upload(request: Request):
        try:
            body = await _json(request)
            url = await asyncio.to_thread(store_asset, body.get("data"))
            return {"ok": True, "url": url}
        except ValueError as exc:
            return JSONResponse(error_body(str(exc)), 400)
        except OSError as exc:
            return JSONResponse(error_body(f"Не удалось сохранить картинку: {exc}"), 500)

    @app.get("/api/pg/theme-editor/asset/{name}")
    async def theme_editor_asset(name: str):
        path = asset_path(name)
        if not path:
            return JSONResponse({"error": "not found"}, 404)
        mime = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[name.rsplit(".", 1)[-1]]
        return FileResponse(str(path), media_type=mime, headers={"Cache-Control": "max-age=31536000, immutable"})
