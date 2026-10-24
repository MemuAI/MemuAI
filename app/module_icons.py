"""Round 39: module icons — the manifest ``icon`` / ``icon_bg``, the user's own icon for any module, a safe SVG.

Manifest ``icon`` may be:

* a glyph name from the MemuAI set (``"music"``) — drawn white on a squircle in ``icon_bg`` (or a colour derived from the id);
* a file of the package (``"icon.svg"``, ``"assets/icon.png"``; ``.svg``/``.png``/``.webp``/``.ico``);
* an inline SVG (``"<svg …>…</svg>"``) or a ``data:image/png|webp|svg+xml;base64,…`` URL (what an export writes).

``icon_bg`` — ``"#rrggbb"`` or a list of 2–3 such colours (a 135° gradient); used behind a glyph name.

The user can replace the icon of any module: ``<modules root>/icons/<id>.<svg|png>`` + ``icons.json``
(``{id: {kind, ext, v, raster}}``) — a raster ``<id>.256.png`` drawn by the page keeps the app shortcut ``.ico``
(module_apps.write_icon) in step with what MemuAI shows. Every SVG — the manifest's and the user's — goes through
:func:`sanitize_svg`: an allow-list of elements and attributes, no script/foreignObject/event handlers, links only
to ``#fragment`` (raster ``data:`` images allowed), no DOCTYPE/ENTITY, size limits.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import modules_host

try:  # route signatures are read by FastAPI from module globals (``from __future__ import annotations``)
    from fastapi import Request
except ImportError:  # pragma: no cover
    Request = Any  # type: ignore[misc,assignment]

GLYPH_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
FILE_RE = re.compile(r"^(?!/)(?!.*\.\.)[A-Za-z0-9_][A-Za-z0-9_./-]{0,120}\.(svg|png|webp|ico)$", re.I)
DATA_RE = re.compile(r"^data:image/(png|webp|svg\+xml);base64,([A-Za-z0-9+/=\s]+)$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

SVG_LIMIT = 256 * 1024          # an uploaded / manifest SVG before cleaning
INLINE_LIMIT = 48 * 1024        # an inline SVG kept in manifest.json after cleaning
RASTER_LIMIT = 768 * 1024       # an uploaded PNG / WebP / ICO
DATA_LIMIT = 256 * 1024         # a data: URL in a manifest

#: The glyph a built-in module falls back to (anything that still reads ``row["icon"]`` as a glyph name).
BUILTIN_GLYPH = {"translator": "globe", "studio": "image", "design": "palette", "benchmarks": "chart",
                 "catalog": "bot", "api-models": "bell", "characters": "users", "music": "music",
                 "learn": "brain"}   # r40-learn

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

_ELEMENTS = {
    "svg", "g", "defs", "title", "desc", "symbol", "use", "path", "rect", "circle", "ellipse", "line", "polyline",
    "polygon", "text", "tspan", "linearGradient", "radialGradient", "stop", "clipPath", "mask", "pattern", "image",
    "filter", "feDropShadow", "feGaussianBlur", "feOffset", "feFlood", "feComposite", "feMerge", "feMergeNode",
    "feColorMatrix", "feBlend", "feMorphology", "style",
}
_ATTRS = {
    "id", "class", "viewBox", "width", "height", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
    "fx", "fy", "fr", "d", "points", "transform", "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width",
    "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset",
    "stroke-opacity", "opacity", "clip-path", "clip-rule", "mask", "filter", "style", "offset", "stop-color",
    "stop-opacity", "gradientUnits", "gradientTransform", "spreadMethod", "patternUnits", "patternContentUnits",
    "patternTransform", "maskUnits", "maskContentUnits", "clipPathUnits", "filterUnits", "primitiveUnits",
    "preserveAspectRatio", "href", "font-family", "font-size", "font-weight", "font-style", "text-anchor",
    "dominant-baseline", "letter-spacing", "dx", "dy", "stdDeviation", "in", "in2", "result", "operator", "k1",
    "k2", "k3", "k4", "values", "type", "mode", "flood-color", "flood-opacity", "radius", "color", "display",
    "visibility", "paint-order", "vector-effect", "shape-rendering", "color-interpolation-filters", "lang",
}
_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I)
_BAD_CSS = re.compile(r"@import|expression\s*\(|javascript:|behavio(u)?r\s*:|-moz-binding|</|<!", re.I)


class IconError(ValueError):
    """A bad icon: the message is shown to the user (ru / en joined by « / »)."""


# ---------------------------------------------------------------------------------------------------- svg

def _local(tag: Any) -> str:
    tag = str(tag)
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _css_ok(text: str) -> bool:
    if _BAD_CSS.search(text):
        return False
    return all(target.strip().startswith("#") for _, target in _URL_RE.findall(text))


def _value_ok(name: str, value: str) -> bool:
    low = value.strip().lower()
    if "javascript:" in low.replace(" ", "") or "data:text" in low:
        return False
    if name == "href":
        return value.startswith("#") or bool(re.match(r"^data:image/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=\s]+$", value))
    if name == "style":
        return _css_ok(value)
    if "url(" in low:
        return all(target.strip().startswith("#") for _, target in _URL_RE.findall(value))
    return True


def _esc(text: str, quote: bool = False) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace('"', "&quot;") if quote else text


def sanitize_svg(text: Any) -> str:
    """A clean, self-contained SVG string, or :class:`IconError`. Never returns script, foreignObject, event
    handlers or external references; the root keeps a ``viewBox`` (made from width/height if missing)."""
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise IconError("SVG не в UTF-8 / The SVG is not UTF-8") from exc
    text = str(text or "").strip()
    if not text or len(text.encode("utf-8")) > SVG_LIMIT:
        raise IconError("SVG пустой или больше 256 КБ / The SVG is empty or larger than 256 KB")
    if re.search(r"<!DOCTYPE|<!ENTITY", text, re.I):
        raise IconError("SVG с DOCTYPE/ENTITY не принимается / An SVG with DOCTYPE/ENTITY is not accepted")
    text = re.sub(r"^<\?xml[^>]*\?>", "", text).strip()
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise IconError("Это не SVG / This is not an SVG") from exc
    if _local(root.tag) != "svg":
        raise IconError("Это не SVG / This is not an SVG")

    out: List[str] = []

    def walk(node: ET.Element, depth: int) -> None:
        name = _local(node.tag)
        if name in ("a", "switch") and depth < 40:  # a link's drawing stays, the link goes
            for child in node:
                walk(child, depth + 1)
            return
        if name not in _ELEMENTS or depth > 40:
            return
        attrs: List[Tuple[str, str]] = []
        for key, value in node.attrib.items():
            attr = _local(key)
            if attr.lower().startswith("on") or attr not in _ATTRS:
                continue
            value = str(value)
            if not _value_ok(attr, value):
                continue
            attrs.append((attr, value))
        if name == "use" and not any(a == "href" for a, _ in attrs):
            return
        if name == "image" and not any(a == "href" for a, _ in attrs):
            return
        if name == "svg" and depth == 0:
            keys = dict(attrs)
            if "viewBox" not in keys:
                try:
                    w = float(re.sub(r"[^0-9.]", "", keys.get("width", "")) or 0)
                    h = float(re.sub(r"[^0-9.]", "", keys.get("height", "")) or 0)
                except ValueError:
                    w = h = 0
                if not (w > 0 and h > 0):
                    w = h = 24
                attrs.append(("viewBox", f"0 0 {w:g} {h:g}"))
            attrs = [(a, v) for a, v in attrs if a not in ("width", "height")]
            attrs.insert(0, ("xmlns", SVG_NS))
        rendered = "".join(f' {a}="{_esc(v, True)}"' for a, v in attrs)
        if name == "style":
            css = "".join(node.itertext())
            if _css_ok(css):
                out.append(f"<style>{_esc(css)}</style>")
            return
        out.append(f"<{name}{rendered}")
        children = list(node)
        body = (node.text or "") if name in ("text", "tspan", "title", "desc") else ""
        if not children and not body:
            out.append("/>")
        else:
            out.append(">" + _esc(body))
            for child in children:
                walk(child, depth + 1)
                if name in ("text", "tspan") and child.tail:
                    out.append(_esc(child.tail))
            out.append(f"</{name}>")

    walk(root, 0)
    result = "".join(out)
    if len(result) < 40 or not re.search(r"<(path|rect|circle|ellipse|polygon|polyline|line|text|image|use)\b", result):
        raise IconError("В SVG нет ничего видимого / The SVG draws nothing")
    return result


# ---------------------------------------------------------------------------------------------------- manifest

def normalize_icon(value: Any) -> str:
    """The manifest ``icon`` kept as-is when it is a glyph name, a package file, a clean inline SVG or a data URL."""
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("<"):
        try:
            clean = sanitize_svg(text)
        except IconError:
            return ""
        return clean if len(clean) <= INLINE_LIMIT else ""
    if text.startswith("data:"):
        return text if DATA_RE.match(text) and len(text) <= DATA_LIMIT else ""
    if FILE_RE.match(text):
        return text
    name = re.sub(r"[^a-z0-9_-]", "", text.lower())[:32]
    return name


def normalize_icon_bg(value: Any) -> List[str]:
    """``icon_bg`` → a list of 1–3 ``#rrggbb`` colours (empty: the host picks)."""
    items = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    return [str(item).lower() for item in items if COLOR_RE.match(str(item or "").strip())][:3]


def icon_kind(icon: str) -> str:
    if not icon:
        return ""
    if icon.startswith("<"):
        return "inline"
    if icon.startswith("data:"):
        return "data"
    if FILE_RE.match(icon):
        return "file"
    return "glyph"


def glyph_of(module_id: str, icon: str) -> str:
    """The glyph name for code that still draws ``row["icon"]`` with the MemuAI glyph set."""
    if icon_kind(icon) == "glyph":
        return icon
    return BUILTIN_GLYPH.get(module_id, "work")


def validate(manifest: Dict[str, Any], files: Optional[Dict[str, Any]] = None) -> List[Tuple[str, str, str]]:
    """``[(level, ru, en)]`` for modules_validate: what is wrong with ``icon`` / ``icon_bg``."""
    problems: List[Tuple[str, str, str]] = []
    raw = manifest.get("icon")
    if raw is not None and not isinstance(raw, str):
        problems.append(("warn", "icon — строка: имя значка, путь к файлу или <svg>.",
                         "icon must be a string: a glyph name, a file path or <svg>."))
    text = str(raw or "").strip() if isinstance(raw, str) else ""
    if text.startswith("<"):
        try:
            clean = sanitize_svg(text)
            if len(clean) > INLINE_LIMIT:
                problems.append(("warn", "Встроенный SVG иконки больше 48 КБ — вынесите его в файл icon.svg.",
                                 "The inline icon SVG is over 48 KB — move it to an icon.svg file."))
            elif len(clean) < len(text) * 0.6:
                problems.append(("info", "Из SVG иконки убраны небезопасные или лишние части.",
                                 "Unsafe or unsupported parts were removed from the icon SVG."))
        except IconError as exc:
            problems.append(("warn", f"icon: {exc}", f"icon: {exc}"))
    elif text.startswith("data:"):
        if not DATA_RE.match(text) or len(text) > DATA_LIMIT:
            problems.append(("warn", "icon: data-URL — только PNG/WebP/SVG в base64 до 256 КБ.",
                             "icon: a data URL must be base64 PNG/WebP/SVG up to 256 KB."))
    elif "." in text or "/" in text:
        if not FILE_RE.match(text):
            problems.append(("warn", "icon: путь — файл пакета .svg/.png/.webp/.ico без «..» и «/» в начале.",
                             "icon: a path must be a package file .svg/.png/.webp/.ico without “..” or a leading “/”."))
        elif files is not None and text not in files:
            problems.append(("warn", f"icon: файла {text} нет в пакете.", f"icon: the file {text} is not in the package."))
    bg = manifest.get("icon_bg")
    if bg is not None:
        items = bg if isinstance(bg, list) else [bg]
        if not items or len(items) > 3 or any(not COLOR_RE.match(str(item or "")) for item in items):
            problems.append(("warn", "icon_bg — цвет #rrggbb или список из 2–3 таких цветов (градиент).",
                             "icon_bg must be a #rrggbb colour or a list of 2–3 of them (a gradient)."))
    return problems


# ---------------------------------------------------------------------------------------------------- user icons

def _icons_dir(root: Path) -> Path:
    return Path(root) / "icons"


def load_user(root: Path) -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads((_icons_dir(root) / "icons.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = data.get("icons") if isinstance(data, dict) else None
    return {k: v for k, v in rows.items() if isinstance(v, dict)} if isinstance(rows, dict) else {}


def _save_user(root: Path, rows: Dict[str, Dict[str, Any]]) -> None:
    folder = _icons_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / "icons.json.tmp"
    tmp.write_text(json.dumps({"icons": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, folder / "icons.json")


def _raster_to_png(raw: bytes) -> bytes:
    """PNG / WebP / ICO bytes → a re-encoded PNG (≤512 px, the largest ICO frame). Re-encoding drops anything else
    the file carried."""
    try:
        from PIL import Image
    except ImportError:
        if raw.startswith(b"\x89PNG"):
            return raw
        raise IconError("Нужен Pillow для WebP/ICO — загрузите PNG или SVG / Pillow is needed for WebP/ICO — upload PNG or SVG")
    try:
        image = Image.open(io.BytesIO(raw))
        if getattr(image, "format", "") == "ICO":
            sizes = sorted(image.info.get("sizes") or [image.size], key=lambda s: s[0] * s[1])
            image.size = sizes[-1]
        image.load()
    except Exception as exc:  # noqa: BLE001 — any decoder error is a bad file
        raise IconError("Не удалось прочитать изображение / Could not read the image") from exc
    if image.width < 16 or image.height < 16 or image.width > 4096 or image.height > 4096:
        raise IconError("Картинка от 16 до 4096 px / The image must be 16–4096 px")
    image = image.convert("RGBA")
    side = max(image.width, image.height)
    if image.width != image.height:  # keep the proportions on a transparent square
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
        image = square
    if side > 512:
        image = image.resize((512, 512), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def decode_upload(data: Any) -> Tuple[str, bytes]:
    """A data URL / an SVG string from the page → ``(ext, bytes)`` ready to store (``svg`` cleaned, rasters → PNG)."""
    text = str(data or "").strip()
    if text.startswith("<"):
        return "svg", sanitize_svg(text).encode("utf-8")
    match = re.match(r"^data:([a-z0-9.+/-]*);base64,([A-Za-z0-9+/=\s]+)$", text, re.I)
    if not match:
        raise IconError("Неизвестный формат файла / Unknown file format")
    try:
        raw = base64.b64decode(match.group(2), validate=False)
    except Exception as exc:  # noqa: BLE001
        raise IconError("Файл повреждён / The file is damaged") from exc
    mime = match.group(1).lower()
    if "svg" in mime or raw.lstrip()[:5] in (b"<svg ", b"<?xml") or raw.lstrip().startswith(b"<svg"):
        return "svg", sanitize_svg(raw).encode("utf-8")
    if len(raw) > RASTER_LIMIT:
        raise IconError("Файл больше 768 КБ / The file is larger than 768 KB")
    return "png", _raster_to_png(raw)


def set_user_icon(root: Path, module_id: str, data: Any, *, raster: Any = "", kind: str = "file",
                  meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Store the user's icon for ``module_id``. ``raster`` — a 256 px PNG data URL the page drew from it (for .ico)."""
    module_id = modules_host._safe_id(module_id)
    if not modules_host.module_exists(Path(root), module_id):
        raise KeyError(module_id)
    ext, body = decode_upload(data)
    folder = _icons_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob(f"{module_id}.*"):
        if old.name != "icons.json":
            try:
                old.unlink()
            except OSError:
                pass
    target = folder / f"{module_id}.{ext}"
    tmp = target.with_suffix("." + ext + ".tmp")
    tmp.write_bytes(body)
    os.replace(tmp, target)
    png = _raster_from(raster) or (body if ext == "png" else b"")
    if png:
        (folder / f"{module_id}.256.png").write_bytes(png)
    rows = load_user(root)
    rows[module_id] = {"kind": kind if kind in ("file", "preset", "emoji", "glyph") else "file", "ext": ext,
                       "v": hashlib.sha1(body).hexdigest()[:10], "raster": bool(png), "at": int(time.time()),
                       **({"meta": _clean_meta(meta)} if meta else {})}
    _save_user(root, rows)
    return rows[module_id]


def _clean_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """What the picker remembers to reopen on the same choice (glyph, emoji, gradient) — short strings only."""
    out: Dict[str, Any] = {}
    for key in ("glyph", "emoji", "preset", "bg"):
        value = meta.get(key) if isinstance(meta, dict) else None
        if isinstance(value, str) and len(value) <= 64:
            out[key] = value
    return out


def _raster_from(data_url: Any) -> bytes:
    from .module_apps import decode_png
    image = decode_png(str(data_url or ""))
    if image is None:
        return b""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def reset_user_icon(root: Path, module_id: str) -> None:
    module_id = modules_host._safe_id(module_id)
    folder = _icons_dir(root)
    for old in folder.glob(f"{module_id}.*") if folder.is_dir() else []:
        if old.name != "icons.json":
            try:
                old.unlink()
            except OSError:
                pass
    rows = load_user(root)
    if rows.pop(module_id, None) is not None:
        _save_user(root, rows)


# ---------------------------------------------------------------------------------------------------- resolve

def _package_icon_file(root: Path, module_id: str, name: str) -> Optional[Path]:
    """A package file named by ``icon``; an edited built-in falls back to the shipped package."""
    folders = [modules_host._package_dir(root, module_id)]
    if module_id in modules_host.BUILTIN_ORDER:
        folders.append(modules_host.builtin_dir() / module_id)
    for folder in folders:
        if folder is None:
            continue
        path = (folder / name).resolve()
        if folder.resolve() in path.parents and path.is_file():
            return path
    return None


def public_fields(root: Path, module_id: str, manifest: Dict[str, Any],
                  user: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """``icon_url`` / ``icon_bg`` / ``icon_custom`` for the public module row."""
    icon = str(manifest.get("icon") or "")
    rows = load_user(root) if user is None else user
    custom = rows.get(module_id)
    url, version = "", ""
    if custom and (_icons_dir(root) / f"{module_id}.{custom.get('ext')}").is_file():
        version = "u" + str(custom.get("v") or "")
    else:
        custom = None
        kind = icon_kind(icon)
        if kind in ("inline", "data"):
            version = hashlib.sha1(icon.encode("utf-8")).hexdigest()[:10]
        elif kind == "file":
            path = _package_icon_file(root, module_id, icon)
            if path is not None:
                stat = path.stat()
                version = hashlib.sha1(f"{path}:{stat.st_mtime_ns}:{stat.st_size}".encode()).hexdigest()[:10]
    if version:
        url = f"/api/pg/modules/{module_id}/icon?v={version}"
    return {"icon_url": url, "icon_bg": list(manifest.get("icon_bg") or []), "icon_custom": bool(custom),
            **({"icon_meta": custom.get("meta")} if custom and custom.get("meta") else {})}


def icon_bytes(root: Path, module_id: str, *, own: bool = False) -> Tuple[bytes, str]:
    """The bytes and media type the icon URL serves (the user's icon first unless ``own``, then the manifest's)."""
    module_id = modules_host._safe_id(module_id)
    custom = None if own else load_user(root).get(module_id)
    if custom:
        path = _icons_dir(root) / f"{module_id}.{custom.get('ext')}"
        if path.is_file():
            return path.read_bytes(), "image/svg+xml" if custom.get("ext") == "svg" else "image/png"
    package = modules_host._package_dir(root, module_id)
    if package is None:
        raise KeyError(module_id)
    manifest = modules_host.normalize_manifest(modules_host._manifest_file(package), module_id=module_id)
    icon = str(manifest.get("icon") or "")
    kind = icon_kind(icon)
    if kind == "inline":
        return icon.encode("utf-8"), "image/svg+xml"
    if kind == "data":
        match = DATA_RE.match(icon)
        raw = base64.b64decode(match.group(2))
        if match.group(1) == "svg+xml":
            return sanitize_svg(raw).encode("utf-8"), "image/svg+xml"
        return _raster_to_png(raw), "image/png"
    if kind == "file":
        path = _package_icon_file(root, module_id, icon)
        if path is not None:
            raw = path.read_bytes()
            if path.suffix.lower() == ".svg":
                return sanitize_svg(raw).encode("utf-8"), "image/svg+xml"
            if path.suffix.lower() == ".png" and len(raw) <= RASTER_LIMIT:
                return raw, "image/png"
            return _raster_to_png(raw), "image/png"
    return letter_svg(module_id, manifest), "image/svg+xml"


#: The pairs of module-icons.js GRADIENTS (without «graphite») — a glyph-only module gets the same one by its id.
_POOL = (("#f08c62", "#8752cf"), ("#ff6a88", "#9b3fd1"), ("#f45fb3", "#fb9440"), ("#ff9a5a", "#e0407a"),
         ("#ffbe4a", "#ee6a3a"), ("#3ad49a", "#168f9f"), ("#29c3d6", "#4258d6"), ("#5f86f7", "#6142d4"),
         ("#c56cf0", "#5b45d8"))


def letter_svg(module_id: str, manifest: Dict[str, Any]) -> bytes:
    """A module with only a glyph name, where no glyph set is at hand (Quick access cards, a bare URL): its first
    letter on the squircle in ``icon_bg`` / the id's gradient — the same tile the page draws around a glyph."""
    colours = list(manifest.get("icon_bg") or [])
    if len(colours) >= 2:
        a, b = colours[0], colours[-1]
    elif colours:
        a = b = colours[0]
    else:
        h = 7
        for ch in module_id:
            h = (h * 31 + ord(ch)) & 0xFFFFFFFF
        a, b = _POOL[h % len(_POOL)]
    letter = _esc((str(manifest.get("title") or module_id).strip()[:1] or "?").upper())
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><defs>'
            f'<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{a}"/><stop offset="1" stop-color="{b}"/></linearGradient>'
            '<radialGradient id="light" cx="0.22" cy="0.1" r="0.7"><stop offset="0" stop-color="#ffffff" stop-opacity=".38"/><stop offset="1" stop-color="#ffffff" stop-opacity="0"/></radialGradient></defs>'
            '<rect x="28" y="28" width="456" height="456" rx="118" fill="url(#bg)"/><rect x="28" y="28" width="456" height="456" rx="118" fill="url(#light)"/>'
            f'<text x="256" y="262" font-size="250" font-weight="700" fill="#ffffff" text-anchor="middle" dominant-baseline="central" '
            f'font-family="Segoe UI, Inter, Arial, sans-serif">{letter}</text></svg>').encode("utf-8")


#: Frames of an app ``.ico`` (module apps: shortcut, window, taskbar). Round 40: built-ins ship ``icon.ico`` drawn from
#: ``icon.svg`` at every one of these sizes (tools/build_module_icons.py) — sharp at 16/24/32 px instead of a 256 px
#: picture squeezed down by the shell.
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
#: Bumped when the way an app ``.ico`` is made changes: installed apps then rebuild theirs on the next start.
ICO_RECIPE = "r40"


def app_icon_source(root: Path, module_id: str) -> Optional[Path]:
    """The file the module app ``.ico`` is made from, or ``None`` (then the page draws one / a letter tile).

    The user's raster (drawn by the page when the icon was set), else the package's ``.ico`` next to its
    ``icon.svg`` (built-ins ship one), a raster package icon, or the ``.png`` next to the ``.svg``."""
    module_id = modules_host._safe_id(module_id)
    candidates: List[Path] = []
    if load_user(root).get(module_id):
        candidates.append(_icons_dir(root) / f"{module_id}.256.png")
    else:
        package = modules_host._package_dir(root, module_id)
        icon = ""
        if package is not None:
            icon = str(modules_host.normalize_manifest(modules_host._manifest_file(package), module_id=module_id).get("icon") or "")
        if icon_kind(icon) == "file":
            stem = re.sub(r"\.(svg|png|webp|ico)$", "", icon, flags=re.I)
            for name in (stem + ".ico", icon, stem + ".png"):
                path = _package_icon_file(root, module_id, name)
                if path is not None and path.suffix.lower() != ".svg":
                    candidates.append(path)
    return next((path for path in candidates if path.is_file()), None)


def source_signature(path: Optional[Path]) -> str:
    """What an installed app's ``.ico`` was made from: a changed icon (or recipe) means a new ``.ico``."""
    if path is None:
        return ""
    try:
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    except OSError:
        return ""
    return f"file:{digest}:{ICO_RECIPE}"


def raster_for(root: Path, module_id: str):
    """A 256 px PIL image of the module's icon (see :func:`app_icon_source`), or ``None``."""
    try:
        from PIL import Image
    except ImportError:
        return None
    path = app_icon_source(root, module_id)
    if path is None:
        return None
    try:
        with Image.open(path) as image:
            if path.suffix.lower() == ".ico" and (256, 256) in image.info.get("sizes", set()):
                image.size = (256, 256)
            image.load()
            return image.convert("RGBA").resize((256, 256), Image.LANCZOS)
    except Exception:  # noqa: BLE001
        return None


def ico_frames(image) -> Dict[int, Any]:
    """Every :data:`ICO_SIZES` frame of a 256 px picture: Lanczos per size, a light unsharp mask at ≤ 48 px
    (a squeezed icon goes soft exactly where the shell shows it most — taskbar, title bar, Start)."""
    from PIL import Image, ImageFilter
    source = image.convert("RGBA")
    frames: Dict[int, Any] = {}
    for size in ICO_SIZES:
        frame = source if source.size == (size, size) else source.resize((size, size), Image.LANCZOS)
        if size <= 48:
            r, g, b, a = frame.split()
            rgb = Image.merge("RGB", (r, g, b)).filter(ImageFilter.UnsharpMask(radius=0.6, percent=70, threshold=1))
            frame = Image.merge("RGBA", (*rgb.split(), a))
        frames[size] = frame
    return frames


def ico_bytes(frames: Dict[int, Any]) -> bytes:
    """A multi-size ``.ico``: 32-bit BMP frames below 256 px (small PNG frames are read blank by parts of the
    shell, Round 33), PNG at 256 px."""
    import struct
    entries = []
    for size in sorted(frames):
        frame = frames[size].convert("RGBA")
        if size >= 256:
            buffer = io.BytesIO()
            frame.save(buffer, "PNG", optimize=True)
            data = buffer.getvalue()
        else:
            header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
            raw = frame.tobytes("raw", "BGRA")
            stride = size * 4
            rows = b"".join(raw[y * stride:(y + 1) * stride] for y in range(size - 1, -1, -1))
            mask_stride = ((size + 31) // 32) * 4
            alpha = frame.getchannel("A").tobytes()
            mask = bytearray()
            for y in range(size - 1, -1, -1):
                bits = bytearray(mask_stride)
                for x in range(size):
                    if alpha[y * size + x] == 0:
                        bits[x // 8] |= 0x80 >> (x % 8)
                mask += bits
            data = header + rows + bytes(mask)
        entries.append((size, data))
    directory = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    for size, data in entries:
        dim = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return directory + b"".join(data for _, data in entries)


# ---------------------------------------------------------------------------------------------------- routes

def register(app, root_fn=None) -> None:
    from fastapi.responses import JSONResponse, Response

    def root() -> Path:
        return Path(root_fn()) if root_fn else modules_host.default_root()

    def err(message: str, status: int = 400):
        return JSONResponse({"ok": False, "error": message}, status_code=status)

    @app.get("/api/pg/modules/{module_id}/icon")
    async def module_icon(module_id: str, v: str = ""):
        try:
            body, media = icon_bytes(root(), module_id)
        except (KeyError, ValueError, OSError):
            return err("no icon", 404)
        # An <img> never runs an SVG's script, but the URL can be opened on its own: lock it down too.
        return Response(body, media_type=media, headers={
            "Cache-Control": "public, max-age=31536000, immutable" if v else "no-cache",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:",
            "X-Content-Type-Options": "nosniff"})

    @app.get("/api/pg/module-icons/builtin/{name}")
    async def module_icons_builtin(name: str):
        """The shipped icon of a built-in module (the picker's «MemuAI set»), whatever the person replaced."""
        match = re.fullmatch(r"([a-z][a-z0-9-]{0,40})\.svg", name or "")
        if not match or match.group(1) not in modules_host.BUILTIN_ORDER:
            return err("no icon", 404)
        path = modules_host.builtin_dir() / match.group(1) / "icon.svg"
        try:
            body = sanitize_svg(path.read_bytes()).encode("utf-8")
        except (OSError, IconError):
            return err("no icon", 404)
        return Response(body, media_type="image/svg+xml", headers={
            "Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

    async def body_of(request: Request) -> Dict[str, Any]:
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            return {}
        return data if isinstance(data, dict) else {}

    def _refresh_app(module_id: str, raster: str) -> None:
        """An installed module app takes the new icon: .ico, shortcuts, the open window."""
        try:
            from . import module_apps
            entry = module_apps.load_store().get("apps", {}).get(module_id)
            if not entry and not (module_apps.app_dir() / f"{module_id}.ico").is_file():
                return
            # Round 40: a new .ico path (Explorer caches by path), shortcuts + pinned buttons re-pointed.
            module_apps.refresh_app_icon(module_id, raster)
            mgr = module_apps.manager(app)
            if mgr is not None and hasattr(mgr, "refresh_icon"):
                mgr.refresh_icon(module_id)
        except Exception:  # noqa: BLE001 — the icon itself is saved; the shortcut catches up on the next repair
            __import__("logging").getLogger("memuai").warning("module app icon not refreshed", exc_info=True)

    @app.post("/api/pg/module-icons/set")
    async def module_icons_set(request: Request):
        data = await body_of(request)
        module_id = str(data.get("id") or "")
        try:
            row = set_user_icon(root(), module_id, data.get("data"), raster=data.get("raster") or "",
                                kind=str(data.get("kind") or "file"), meta=data.get("meta"))
        except KeyError:
            return err("module not found", 404)
        except (IconError, ValueError) as exc:
            return err(str(exc))
        _refresh_app(module_id, str(data.get("raster") or ""))
        return {"ok": True, "icon": row}

    @app.post("/api/pg/module-icons/reset")
    async def module_icons_reset(request: Request):
        data = await body_of(request)
        module_id = str(data.get("id") or "")
        try:
            reset_user_icon(root(), module_id)
        except ValueError as exc:
            return err(str(exc))
        _refresh_app(module_id, str(data.get("raster") or ""))
        return {"ok": True}

    @app.post("/api/pg/module-icons/sanitize")
    async def module_icons_sanitize(request: Request):
        """For the module editor: what an inline/uploaded SVG becomes after cleaning."""
        data = await body_of(request)
        try:
            return {"ok": True, "svg": sanitize_svg(data.get("svg"))}
        except IconError as exc:
            return err(str(exc))
