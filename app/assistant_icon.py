"""MemuAI window/taskbar/tray/shortcut icon that follows the assistant's look (Round 33).

``POST /api/window/icon`` (app/playground_api.py) calls :func:`sync`. The page
calls that endpoint after every preference change, so everything here is
idempotent and cheap when nothing changed:

* the default look (no photo, the default gradient) uses the shipped
  ``memuai-icon.ico`` — the hinted app icon, not a generated one;
* a photo → the photo in a circle; another gradient → the MemuAI speech
  bubble with the star in that gradient (the shape of the app icon);
* generated files get a content-hashed name (``assistant-<hash>.ico``),
  are written atomically and only when the look changes — Explorer never
  sees a half-written icon nor a cached icon for a changed file;
* window icons go through :data:`app.win_app_identity.WINDOW_ICONS`
  (handles freed only after replacement), shortcuts are updated in the
  background only on a change (or when they point at a missing file).

State: ``DATA_DIR/memuai/assistant-icon.json`` (``signature``, ``ico``, ``png``).
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

from . import win_app_identity as identity

log = logging.getLogger("memuai.identity")

DEFAULT_GRADIENT = "linear-gradient(135deg,#cc785c,#e89a7e)"
DEFAULT_COLORS = ("#cc785c", "#e89a7e")
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
STATE_NAME = "assistant-icon.json"
KEEP_GENERATED = 3

_lock = threading.Lock()


def colors_of(gradient: str) -> Tuple[str, str]:
    found = re.findall(r"#[0-9a-fA-F]{6}", gradient or "")[:2]
    if not found:
        return DEFAULT_COLORS
    if len(found) == 1:
        found.append(found[0])
    return found[0].lower(), found[1].lower()


def is_default(avatar: str, gradient: str) -> bool:
    return not avatar and colors_of(gradient) == DEFAULT_COLORS


def signature(avatar: str, gradient: str) -> str:
    if is_default(avatar, gradient):
        return "default"
    key = avatar if avatar else "gradient:" + ",".join(colors_of(gradient))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ drawing
def _rgb(color: str) -> Tuple[int, int, int]:
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def _star_points(cx: float, cy: float, r: float, inner: float = 0.22, steps: int = 24):
    """Polygon of the concave four-point star (quadratic sides sampled)."""
    k = r * inner
    tips = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    ctrl = [(cx + k, cy - k), (cx + k, cy + k), (cx - k, cy + k), (cx - k, cy - k)]
    points = []
    for i in range(4):
        p0, p1, p2 = tips[i], ctrl[i], tips[(i + 1) % 4]
        for step in range(steps):
            t = step / steps
            points.append(((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
                           (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]))
    return points


def _bubble_mask(size: int):
    """The MemuAI speech-bubble outline (same proportions as build_memuai_icon full drawing)."""
    from PIL import Image, ImageDraw
    scale = size / 512
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((32 * scale, 32 * scale, 480 * scale, 416 * scale), radius=118 * scale, fill=255)
    draw.polygon([(150 * scale, 410 * scale), (200 * scale, 416 * scale), (62 * scale, 482 * scale),
                  (32 * scale, 330 * scale), (32 * scale, 300 * scale)], fill=255)
    return mask


def render_image(avatar: str, gradient: str, size: int = 256):
    """The assistant icon as an RGBA image (photo circle or gradient bubble)."""
    from PIL import Image, ImageDraw, ImageOps
    if avatar.startswith("data:image/") and "," in avatar:
        photo = Image.open(io.BytesIO(base64.b64decode(avatar.split(",", 1)[1]))).convert("RGBA")
        big = size * 4
        photo = ImageOps.fit(photo, (big, big), method=Image.Resampling.LANCZOS)
        mask = Image.new("L", (big, big), 0)
        ImageDraw.Draw(mask).ellipse((big * 0.02, big * 0.02, big * 0.98, big * 0.98), fill=255)
        photo.putalpha(mask)
        return photo.resize((size, size), Image.Resampling.LANCZOS)
    from PIL import ImageChops
    start, end = (_rgb(c) for c in colors_of(gradient))
    big = size * 4
    vertical = Image.linear_gradient("L").resize((big, big))
    horizontal = vertical.transpose(Image.Transpose.ROTATE_90)
    diagonal = ImageChops.add(horizontal, vertical, scale=2.0)      # (x + y) / 2, top-left → bottom-right
    fill = Image.composite(Image.new("RGBA", (big, big), end + (255,)),
                           Image.new("RGBA", (big, big), start + (255,)), diagonal)
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    image.paste(fill, mask=_bubble_mask(big))
    scale = big / 512
    ImageDraw.Draw(image).polygon(_star_points(256 * scale, 224 * scale, 150 * scale), fill=(255, 255, 255, 250))
    return image.resize((size, size), Image.Resampling.LANCZOS)


def _write_ico(image, path: Path) -> None:
    """Multi-size .ico, BMP entries below 256 px (see build_memuai_icon.write_ico)."""
    import struct
    from PIL import Image
    entries = []
    for size in ICON_SIZES:
        frame = image.resize((size, size), Image.Resampling.LANCZOS).convert("RGBA")
        if size >= 256:
            buffer = io.BytesIO()
            frame.save(buffer, "PNG")
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
    blobs = []
    for size, data in entries:
        dim = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs.append(data)
    _atomic_write(path, directory + b"".join(blobs))


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    os.replace(temporary, path)


# ------------------------------------------------------------------ files
def icon_dir(data_dir) -> Path:
    return Path(data_dir) / "memuai"


def load_state(data_dir) -> Dict[str, str]:
    try:
        data = json.loads((icon_dir(data_dir) / STATE_NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(data_dir, state: Dict[str, str]) -> None:
    folder = icon_dir(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    _atomic_write(folder / STATE_NAME, json.dumps(state, ensure_ascii=False, indent=1).encode("utf-8"))


def bundled_ico() -> str:
    return identity.bundled_file("memuai-icon.ico")


def bundled_png() -> str:
    return identity.bundled_file("memuai-icon.png")


def current_icon(data_dir) -> Tuple[str, str]:
    """(ico, png) the MemuAI windows/tray should show now (custom when generated, else bundled)."""
    state = load_state(data_dir)
    ico, png = state.get("ico", ""), state.get("png", "")
    if state.get("signature", "default") != "default" and ico and os.path.isfile(ico):
        return ico, png if png and os.path.isfile(png) else ""
    return bundled_ico(), bundled_png()


def ensure_files(data_dir, avatar: str, gradient: str) -> Tuple[str, str, bool]:
    """Make sure the icon files for this look exist. Returns (ico, png, changed)."""
    sig = signature(avatar, gradient)
    with _lock:
        state = load_state(data_dir)
        previous = state.get("signature", "")
        if sig == "default":
            changed = previous not in ("", "default")
            if previous != "default":
                state.update({"signature": "default", "ico": "", "png": ""})
                _save_state(data_dir, state)
            return bundled_ico(), bundled_png(), changed or previous == ""
        folder = icon_dir(data_dir)
        ico = folder / f"assistant-{sig}.ico"
        png = folder / "assistant.png"
        if previous == sig and ico.is_file() and png.is_file():
            return str(ico), str(png), False
        folder.mkdir(parents=True, exist_ok=True)
        image = render_image(avatar, gradient, 256)
        _write_ico(image, ico)
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        _atomic_write(png, buffer.getvalue())
        state.update({"signature": sig, "ico": str(ico), "png": str(png)})
        _save_state(data_dir, state)
        _prune(folder, keep=str(ico))
        return str(ico), str(png), True


def _prune(folder: Path, keep: str) -> None:
    """Keep the newest few generated icons (an unknown shortcut may still point at one)."""
    generated = sorted(folder.glob("assistant-*.ico"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in generated[KEEP_GENERATED:]:
        if os.path.normcase(str(path)) != os.path.normcase(keep):
            try:
                path.unlink()
            except OSError:
                pass


# ------------------------------------------------------------------ apply
_shortcut_thread: Optional[threading.Thread] = None


def schedule_shortcut_sync(data_dir, ico: str) -> None:
    """Update the shortcuts in the background (COM + disk; never on the request thread)."""
    global _shortcut_thread
    if not identity.is_windows() and identity.api() is None:
        return
    desired = "" if os.path.normcase(ico or "") == os.path.normcase(bundled_ico() or "") else ico

    def work() -> None:
        try:
            identity.sync_shortcut_icons(desired, "MemuAI.exe", owned_dirs=[str(icon_dir(data_dir))])
        except Exception as exc:  # noqa: BLE001
            log.debug("shortcut icons not synced: %s", exc)

    thread = threading.Thread(target=work, name="MemuAI shortcut icons", daemon=True)
    _shortcut_thread = thread
    thread.start()


def apply_to_window(hwnd: int, data_dir) -> bool:
    """Startup / restore: give the window the current icon (no file writes)."""
    ico, _png = current_icon(data_dir)
    return identity.WINDOW_ICONS.apply(hwnd, ico) if ico else False


def sync(data_dir, avatar: str, gradient: str, hwnd: int) -> Dict[str, object]:
    """Bring files, window, tray and shortcuts in line with the assistant's look."""
    ico, png, changed = ensure_files(data_dir, avatar, gradient)
    ok = identity.WINDOW_ICONS.apply(hwnd, ico) if hwnd and ico else False
    if changed:
        try:
            from . import desktop
            desktop.refresh_tray_icon(png or bundled_png(), ico)
        except Exception as exc:  # noqa: BLE001
            log.debug("tray icon not refreshed: %s", exc)
        schedule_shortcut_sync(data_dir, ico)
    return {"ok": bool(ok), "changed": bool(changed), "default": signature(avatar, gradient) == "default"}
