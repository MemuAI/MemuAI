"""Round 32 (photo): image decoding / encoding for the MemuNote photo editor (Pillow; PSD by ``formats.psd``).

The page decodes what the browser engine can (PNG, JPEG, WebP, GIF frame 0, BMP, ICO, AVIF, SVG) itself; this module
is used for the rest and for anything the page asks explicitly:

* :func:`decode` — any Pillow-readable file (TIFF incl. multi-page, PSD/PSB merged, ICO largest size, TGA, PCX, PPM,
  DDS, HEIC/HEIF when ``pillow-heif`` is installed…) → ``{width, height, png, frames, format}``; PSD → layers too.
* :func:`frames` — every frame of GIF / APNG / WebP / TIFF / ICO as PNGs (+ durations), capped.
* :func:`encode` — page PNG → PNG / JPEG / WebP / AVIF / BMP / TIFF / ICO / GIF / PDF / TGA bytes; PSD from layers.
"""
from __future__ import annotations

import base64
import io
from typing import Any, Dict, List, Optional

MAX_PIXELS = 200_000_000
MAX_FRAMES = 300
ENCODERS = ("png", "jpeg", "webp", "avif", "bmp", "tiff", "ico", "gif", "pdf", "tga")
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

class ImageFormatError(ValueError):
    def __init__(self, message: str, code: str = "unsupported") -> None:
        super().__init__(message)
        self.code = code

def _pil():
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        import pillow_heif                  # type: ignore
        pillow_heif.register_heif_opener()
    except Exception:  # noqa: BLE001
        pass
    return Image

def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")

def _to_png(img, level: int = 1) -> bytes:
    if img.mode not in ("RGBA", "RGB", "L", "LA"):
        img = img.convert("RGBA")
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=level)
    return buf.getvalue()

def capabilities() -> Dict[str, Any]:
    try:
        Image = _pil()
        from PIL import features
        heif = False
        try:
            import pillow_heif  # type: ignore  # noqa: F401
            heif = True
        except Exception:  # noqa: BLE001
            pass
        return {"pillow": True, "avif": bool(features.check("avif")), "webp": bool(features.check("webp")),
                "heif": heif, "encoders": [e for e in ENCODERS if e != "avif" or features.check("avif")],
                "version": getattr(Image, "__version__", "")}
    except Exception:  # noqa: BLE001 - Pillow missing in a stripped build: the page uses only browser codecs
        return {"pillow": False, "avif": False, "webp": False, "heif": False, "encoders": [], "version": ""}

def decode(path: str, frame: int = 0) -> Dict[str, Any]:
    with open(path, "rb") as handle:
        head = handle.read(4)
    if head == b"8BPS":
        from . import psd
        data = open(path, "rb").read()
        try:
            doc = psd.read_psd(data)
            png = doc["merged"]
            if png is None:
                png = _merged_with_pillow(path)
            return {"format": "PSD", "width": doc["width"], "height": doc["height"], "png": b64(png) if png else "",
                    "frames": 1, "layers": _b64_layers(doc["layers"])}
        except psd.UnsupportedPSD:
            png = _merged_with_pillow(path)
            img = _pil().open(io.BytesIO(png))
            return {"format": "PSD", "width": img.width, "height": img.height, "png": b64(png), "frames": 1,
                    "layers": [], "warning": "psd_flattened"}
    Image = _pil()
    try:
        img = Image.open(path)
    except Image.DecompressionBombError as exc:
        raise ImageFormatError(str(exc), "too_large") from exc
    except Exception as exc:  # noqa: BLE001
        ext = path.lower().rsplit(".", 1)[-1]
        if ext in ("heic", "heif"):
            raise ImageFormatError("HEIC/HEIF needs the optional pillow-heif package", "heic") from exc
        raise ImageFormatError(f"cannot read the image: {exc}", "unreadable") from exc
    fmt = img.format or ""
    n = int(getattr(img, "n_frames", 1) or 1)
    if fmt == "ICO":
        sizes = sorted(img.info.get("sizes") or img.ico.sizes(), key=lambda s: s[0] * s[1])
        if sizes:
            img.size = sizes[-1]
        n = len(sizes) or 1
    elif n > 1 and frame:
        img.seek(max(0, min(n - 1, frame)))
    img.load()
    out = img.convert("RGBA") if img.mode != "RGBA" else img
    return {"format": fmt, "width": out.width, "height": out.height, "png": b64(_to_png(out)), "frames": n,
            "dpi": list(img.info.get("dpi") or [])[:2]}

def _merged_with_pillow(path: str) -> bytes:
    Image = _pil()
    img = Image.open(path)
    img.load()
    return _to_png(img.convert("RGBA"))

def _b64_layers(layers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for layer in layers:
        item = dict(layer)
        if isinstance(item.get("png"), bytes):
            item["png"] = b64(item["png"])
        if isinstance(item.get("mask"), dict) and isinstance(item["mask"].get("png"), bytes):
            item["mask"] = {**item["mask"], "png": b64(item["mask"]["png"])}
        if item.get("children"):
            item["children"] = _b64_layers(item["children"])
        out.append(item)
    return out

def frames(path: str, limit: int = MAX_FRAMES) -> Dict[str, Any]:
    Image = _pil()
    img = Image.open(path)
    rows: List[Dict[str, Any]] = []
    if img.format == "ICO":
        for size in sorted(img.ico.sizes(), key=lambda s: -s[0] * s[1])[:limit]:
            frame = img.ico.getimage(size)
            rows.append({"png": b64(_to_png(frame.convert("RGBA"))), "width": frame.width, "height": frame.height,
                         "label": f"{size[0]}×{size[1]}"})
        return {"format": "ICO", "frames": rows, "total": len(rows)}
    total = int(getattr(img, "n_frames", 1) or 1)
    for i in range(min(total, limit)):
        img.seek(i)
        frame = img.convert("RGBA")
        rows.append({"png": b64(_to_png(frame)), "width": frame.width, "height": frame.height,
                     "duration": int(img.info.get("duration") or 0)})
    return {"format": img.format or "", "frames": rows, "total": total}

def encode(png: bytes, fmt: str, quality: int = 90, background: str = "#ffffff",
           extra: Optional[Dict[str, Any]] = None) -> bytes:
    """Page PNG (RGBA) → bytes of *fmt*. Formats without alpha are flattened on *background*."""
    fmt = fmt.lower().replace("jpg", "jpeg").replace("tif", "tiff").replace("tifff", "tiff")
    if fmt not in ENCODERS:
        raise ImageFormatError(f"unsupported format {fmt}")
    extra = extra or {}
    Image = _pil()
    img = Image.open(io.BytesIO(png))
    img.load()
    img = img.convert("RGBA")
    quality = max(1, min(100, int(quality or 90)))
    buf = io.BytesIO()

    def flat():
        bg = Image.new("RGB", img.size, _hex(background))
        bg.paste(img, mask=img.split()[3])
        return bg

    if fmt == "png":
        img.save(buf, "PNG", optimize=bool(extra.get("optimize")), compress_level=6)
    elif fmt == "jpeg":
        flat().save(buf, "JPEG", quality=quality, optimize=True, progressive=bool(extra.get("progressive", True)),
                    subsampling=0 if quality >= 90 else 2)
    elif fmt == "webp":
        img.save(buf, "WEBP", quality=quality, lossless=bool(extra.get("lossless")), method=4)
    elif fmt == "avif":
        img.save(buf, "AVIF", quality=quality)
    elif fmt == "bmp":
        (img if extra.get("alpha") else flat()).save(buf, "BMP")
    elif fmt == "tiff":
        img.save(buf, "TIFF", compression="tiff_lzw")
    elif fmt == "ico":
        sizes = [s for s in (extra.get("sizes") or ICO_SIZES) if isinstance(s, int) and 8 <= s <= 256]
        side = max(img.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
        square.save(buf, "ICO", sizes=[(s, s) for s in sizes or ICO_SIZES])
    elif fmt == "gif":
        alpha = img.split()[3]
        rgb = flat() if not extra.get("transparent", True) else img.convert("RGB")
        pal = rgb.quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)
        if extra.get("transparent", True) and alpha.getextrema()[0] < 128:
            mask = alpha.point(lambda v: 255 if v < 128 else 0)
            pal.paste(255, mask=mask)
            pal.save(buf, "GIF", transparency=255)
        else:
            pal.save(buf, "GIF")
    elif fmt == "pdf":
        flat().save(buf, "PDF", resolution=float(extra.get("dpi") or 96))
    elif fmt == "tga":
        img.save(buf, "TGA", rle=True)
    return buf.getvalue()

def encode_gif_animation(pngs: List[bytes], durations: List[int], loop: int = 0) -> bytes:
    Image = _pil()
    frames_ = [Image.open(io.BytesIO(p)).convert("RGBA") for p in pngs[:MAX_FRAMES]]
    if not frames_:
        raise ImageFormatError("no frames")
    buf = io.BytesIO()
    frames_[0].save(buf, "GIF", save_all=True, append_images=frames_[1:], duration=durations or 100, loop=loop,
                    disposal=2)
    return buf.getvalue()

def _hex(value: str):
    s = str(value or "#ffffff").lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (255, 255, 255)
