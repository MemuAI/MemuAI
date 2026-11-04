# -*- coding: utf-8 -*-
"""Image utilities (Pillow): convert, resize, crop, compress, strip metadata, EXIF, filters, watermark, favicon, GIF…"""
from __future__ import annotations

import io
import math
import zipfile
from typing import Any, Dict, List, Tuple

from .core import O, UtilError, as_int, human_size, parse_color, table, utility
from .t_gen import font, save_image

IMG_ACCEPT = "image/*,.heic,.heif,.avif,.ico,.tif,.tiff,.psd"
FORMATS = [("png", "PNG", "PNG"), ("jpg", "JPG", "JPG"), ("webp", "WebP", "WebP"), ("avif", "AVIF", "AVIF"), ("gif", "GIF", "GIF"),
           ("bmp", "BMP", "BMP"), ("tiff", "TIFF", "TIFF"), ("ico", "ICO", "ICO")]

def _pil():
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise UtilError("Библиотека Pillow недоступна", "Pillow is not available")
    try:
        import pillow_heif  # type: ignore
        pillow_heif.register_heif_opener()
    except Exception:  # noqa: BLE001 - HEIC support is optional
        pass
    return Image, ImageOps

def open_image(f, exif_turn: bool = True):
    Image, ImageOps = _pil()
    try:
        img = Image.open(io.BytesIO(f.data))
        img.load()
    except Exception as exc:  # noqa: BLE001
        raise UtilError(f"«{f.name}» не открывается как изображение: {exc}", f"“{f.name}” cannot be opened as an image: {exc}")
    if exif_turn:
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:  # noqa: BLE001
            pass
    return img

def out_format(ctx, f, wanted: str = "") -> str:
    fmt = (wanted or ctx.o.get("format") or "same").lower()
    if fmt == "same":
        fmt = {"jpeg": "jpg", "tif": "tiff"}.get(f.ext, f.ext)
        if fmt not in ("png", "jpg", "webp", "gif", "bmp", "tiff", "avif", "ico"):
            fmt = "png"
    return fmt

def emit(ctx, f, img, fmt: str, suffix: str = "", quality: int = 90) -> None:
    if fmt == "avif":
        from PIL import features
        if not features.check("avif"):
            raise UtilError("Эта сборка Pillow не умеет AVIF — выберите WebP", "This Pillow build has no AVIF — choose WebP")
    ctx.out_bytes(f"{f.stem}{suffix}.{fmt}", save_image(img, fmt, quality))

def _same_opt():
    return O("format", "choice", "Формат результата", "Output format", "same", choices=[("same", "Как у исходного", "Same as input")] + FORMATS)

def _each(ctx):
    if not ctx.files:
        raise UtilError("Добавьте изображение", "Add an image")
    return ctx.files

@utility("image.convert", "image", "Конвертер изображений", "Image converter",
         "PNG, JPG, WebP, AVIF, GIF, BMP, TIFF, ICO, а также HEIC с iPhone — пачкой, с выбором качества.",
         "PNG, JPG, WebP, AVIF, GIF, BMP, TIFF, ICO and iPhone HEIC — in batches, with quality control.",
         input="files", accept=IMG_ACCEPT,
         opts=[O("format", "choice", "В формат", "To format", "webp", choices=FORMATS), O("quality", "int", "Качество (JPG/WebP/AVIF)", "Quality (JPG/WebP/AVIF)", 88, min=1, max=100),
               O("bg", "color", "Фон вместо прозрачности (JPG/BMP)", "Background for transparency (JPG/BMP)", "#ffffff")],
         ai_default=True, keys="конвертировать картинку png jpg webp heic avif ico формат изображения image convert")
def image_convert(ctx):
    Image, _ = _pil()
    fmt, total_in, total_out = ctx.o["format"], 0, 0
    for f in _each(ctx):
        img = open_image(f)
        if fmt in ("jpg", "bmp") and img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, tuple(parse_color(ctx.o["bg"], (255, 255, 255))[:3]))
            rgba = img.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[3])
            img = bg
        if getattr(img, "n_frames", 1) > 1 and fmt in ("gif", "webp"):
            ctx.out_bytes(f"{f.stem}.{fmt}", _animated(img, fmt, int(ctx.o["quality"])))
        else:
            emit(ctx, f, img, fmt, quality=int(ctx.o["quality"]))
        total_in += f.size
        total_out += ctx.outputs[-1].stat().st_size
    return {"note": ctx.tr(f"{len(ctx.files)} файл(ов): {human_size(total_in)} → {human_size(total_out)}", f"{len(ctx.files)} file(s): {human_size(total_in, True)} → {human_size(total_out, True)}")}

def _animated(img, fmt: str, quality: int) -> bytes:
    from PIL import ImageSequence
    frames, durations = [], []
    for frame in ImageSequence.Iterator(img):
        frames.append(frame.convert("RGBA"))
        durations.append(frame.info.get("duration", 100))
    buf = io.BytesIO()
    if fmt == "gif":
        frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=durations, loop=0, disposal=2)
    else:
        frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:], duration=durations, loop=0, quality=quality)
    return buf.getvalue()

@utility("image.resize", "image", "Изменить размер", "Resize images",
         "По ширине, высоте, проценту или «вписать в рамку»; пропорции сохраняются, можно обрезать под точный размер.",
         "By width, height, percent or “fit in a box”; keeps proportions, can crop to an exact size.",
         input="files", accept=IMG_ACCEPT,
         opts=[O("mode", "choice", "Как", "Mode", "fit", choices=[("fit", "Вписать в W×H", "Fit into W×H"), ("cover", "Заполнить W×H с обрезкой", "Fill W×H (crop)"),
                                                               ("exact", "Ровно W×H (растянуть)", "Exactly W×H (stretch)"), ("width", "По ширине", "By width"),
                                                               ("height", "По высоте", "By height"), ("percent", "В процентах", "By percent")]),
               O("width", "int", "Ширина", "Width", 1280, min=1, max=20000, show="mode=fit|cover|exact|width"),
               O("height", "int", "Высота", "Height", 1280, min=1, max=20000, show="mode=fit|cover|exact|height"),
               O("percent", "int", "Процент", "Percent", 50, min=1, max=1000, show="mode=percent"),
               O("noup", "bool", "Не увеличивать маленькие", "Do not upscale", True), _same_opt(),
               O("quality", "int", "Качество", "Quality", 90, min=1, max=100)],
         ai_default=True, keys="размер уменьшить увеличить resize ширина высота масштаб")
def image_resize(ctx):
    Image, ImageOps = _pil()
    for f in _each(ctx):
        img = open_image(f)
        w, h = img.size
        mode, W, H = ctx.o["mode"], int(ctx.o["width"]), int(ctx.o["height"])
        if mode == "percent":
            size = (max(1, round(w * int(ctx.o["percent"]) / 100)), max(1, round(h * int(ctx.o["percent"]) / 100)))
        elif mode == "width":
            size = (W, max(1, round(h * W / w)))
        elif mode == "height":
            size = (max(1, round(w * H / h)), H)
        elif mode == "exact":
            size = (W, H)
        elif mode == "cover":
            img = ImageOps.fit(img, (W, H), Image.LANCZOS)
            size = img.size
        else:
            k = min(W / w, H / h)
            size = (max(1, round(w * k)), max(1, round(h * k)))
        if ctx.o["noup"] and mode not in ("exact", "cover") and (size[0] > w or size[1] > h):
            size = (w, h)
        if img.size != size:
            img = img.resize(size, Image.LANCZOS)
        emit(ctx, f, img, out_format(ctx, f), f"-{size[0]}x{size[1]}", int(ctx.o["quality"]))
    return ""

def _parse_ratio(text: str):
    import re
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[:x×/]\s*(\d+(?:\.\d+)?)\s*", text or "")
    return (float(m.group(1)), float(m.group(2))) if m else None

@utility("image.crop", "image", "Обрезать", "Crop",
         "По пропорции от центра (1:1, 16:9, 4:5…) или по точным координатам x, y, ширина, высота.",
         "By aspect ratio from the center (1:1, 16:9, 4:5…) or by exact x, y, width, height.",
         input="files", accept=IMG_ACCEPT,
         opts=[O("mode", "choice", "Как", "Mode", "ratio", choices=[("ratio", "По пропорции", "By aspect ratio"), ("box", "По координатам", "By coordinates")]),
               O("ratio", "text", "Пропорция", "Ratio", "1:1", show="mode=ratio"),
               O("anchor", "choice", "Привязка", "Anchor", "center", show="mode=ratio", choices=[("center", "Центр", "Center"), ("top", "Верх", "Top"), ("bottom", "Низ", "Bottom"), ("left", "Лево", "Left"), ("right", "Право", "Right")]),
               O("x", "int", "X", "X", 0, min=0, show="mode=box"), O("y", "int", "Y", "Y", 0, min=0, show="mode=box"),
               O("w", "int", "Ширина", "Width", 500, min=1, show="mode=box"), O("h", "int", "Высота", "Height", 500, min=1, show="mode=box"), _same_opt()],
         ai_default=True, keys="обрезать crop кадрировать пропорция квадрат")
def image_crop(ctx):
    for f in _each(ctx):
        img = open_image(f)
        w, h = img.size
        if ctx.o["mode"] == "box":
            x, y = int(ctx.o["x"]), int(ctx.o["y"])
            box = (min(x, w - 1), min(y, h - 1), min(w, x + int(ctx.o["w"])), min(h, y + int(ctx.o["h"])))
        else:
            r = _parse_ratio(ctx.o["ratio"])
            if not r:
                raise UtilError("Пропорция в виде 16:9", "Ratio like 16:9")
            target = r[0] / r[1]
            if w / h > target:
                nw, nh = round(h * target), h
            else:
                nw, nh = w, round(w / target)
            anchor = ctx.o["anchor"]
            x = {"left": 0, "right": w - nw}.get(anchor, (w - nw) // 2)
            y = {"top": 0, "bottom": h - nh}.get(anchor, (h - nh) // 2)
            box = (x, y, x + nw, y + nh)
        img = img.crop(box)
        emit(ctx, f, img, out_format(ctx, f), f"-crop")
    return ""

@utility("image.rotate", "image", "Повернуть и отразить", "Rotate and flip",
         "90°, 180°, 270° или любой угол; зеркально по горизонтали/вертикали; выпрямить по EXIF.",
         "90°, 180°, 270° or any angle; mirror horizontally/vertically; straighten by EXIF.",
         input="files", accept=IMG_ACCEPT,
         opts=[O("op", "choice", "Действие", "Action", "r90", choices=[("r90", "Повернуть на 90° по часовой", "Rotate 90° clockwise"), ("r270", "Повернуть на 90° против часовой", "Rotate 90° counter-clockwise"),
                                                                    ("r180", "Повернуть на 180°", "Rotate 180°"), ("angle", "Свой угол", "Custom angle"),
                                                                    ("fliph", "Отразить по горизонтали", "Flip horizontally"), ("flipv", "Отразить по вертикали", "Flip vertically"),
                                                                    ("exif", "Только выпрямить по EXIF", "Only apply EXIF orientation")]),
               O("angle", "number", "Угол, ° (по часовой)", "Angle, ° (clockwise)", 15, min=-360, max=360, show="op=angle"),
               O("bg", "color", "Фон углов", "Corner background", "#ffffff", show="op=angle"), _same_opt()],
         ai_default=True, keys="повернуть отразить зеркально rotate flip mirror")
def image_rotate(ctx):
    Image, _ = _pil()
    for f in _each(ctx):
        img = open_image(f)
        op = ctx.o["op"]
        if op == "r90":
            img = img.transpose(Image.Transpose.ROTATE_270)
        elif op == "r270":
            img = img.transpose(Image.Transpose.ROTATE_90)
        elif op == "r180":
            img = img.transpose(Image.Transpose.ROTATE_180)
        elif op == "fliph":
            img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        elif op == "flipv":
            img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        elif op == "angle":
            fill = tuple(parse_color(ctx.o["bg"], (255, 255, 255))[:3])
            if img.mode == "RGBA":
                fill = fill + (0,)
            img = img.rotate(-float(ctx.o["angle"]), resample=Image.BICUBIC, expand=True, fillcolor=fill)
        emit(ctx, f, img, out_format(ctx, f), "-rotated")
    return ""

def _has_alpha(img) -> bool:
    """True when the picture really uses transparency (not just an opaque alpha channel)."""
    if img.mode in ("RGBA", "LA", "PA"):
        try:
            return img.getchannel("A").getextrema()[0] < 250
        except (ValueError, OSError):
            return True
    return img.mode == "P" and "transparency" in img.info

@utility("image.compress", "image", "Сжать изображения", "Compress images",
         "Уменьшает вес фото для почты и сайтов: по качеству или до целевого размера в КБ, с уменьшением огромных фото.",
         "Shrinks photos for mail and websites: by quality or down to a target size in KB, scaling down huge photos.",
         input="files", accept=IMG_ACCEPT,
         opts=[O("format", "choice", "Формат", "Format", "jpg", choices=[("jpg", "JPG", "JPG"), ("webp", "WebP", "WebP"), ("png", "PNG (без потерь, палитра)", "PNG (lossless/palette)"), ("same", "Как у исходного", "Same as input")]),
               O("target", "int", "Целевой размер, КБ (0 — по качеству)", "Target size, KB (0 = by quality)", 0, min=0, max=100000),
               O("quality", "int", "Качество", "Quality", 78, min=5, max=100, show="target=0"),
               O("maxside", "int", "Макс. сторона, px (0 — не менять)", "Max side, px (0 = keep)", 2560, min=0, max=20000)],
         ai_default=True, keys="сжать уменьшить вес оптимизировать compress optimize jpg размер файла")
def image_compress(ctx):
    Image, _ = _pil()
    rows, notes = [], set()
    for f in _each(ctx):
        img = open_image(f)
        side = int(ctx.o["maxside"])
        if side and max(img.size) > side:
            k = side / max(img.size)
            img = img.resize((round(img.width * k), round(img.height * k)), Image.LANCZOS)
        fmt = out_format(ctx, f)
        if fmt in ("jpg", "jpeg") and _has_alpha(img):
            fmt = "webp"  # JPEG would paint the transparent parts white: WebP keeps them and is as small
            notes.add(ctx.tr("Картинки с прозрачностью сохранены в WebP, чтобы не потерять прозрачность",
                             "Images with transparency were saved as WebP to keep the transparency"))
        target = int(ctx.o["target"]) * 1024
        if fmt == "png":
            q = img.convert("RGBA").quantize(256, method=Image.Quantize.FASTOCTREE) if img.mode in ("RGBA", "LA", "P") else img.convert("RGB").quantize(256)
            data = save_image(q, "png")
        elif target:
            lo, hi, best = 5, 95, None
            while lo <= hi:
                mid = (lo + hi) // 2
                data = save_image(img, fmt, mid)
                if len(data) <= target:
                    best, lo = data, mid + 1
                else:
                    hi = mid - 1
            data = best or save_image(img, fmt, 5)
            while len(data) > target and min(img.size) > 64:
                img = img.resize((round(img.width * 0.85), round(img.height * 0.85)), Image.LANCZOS)
                data = save_image(img, fmt, 60)
        else:
            data = save_image(img, fmt, int(ctx.o["quality"]))
        same = f.ext.lstrip(".").lower().replace("jpeg", "jpg") == fmt
        if same and len(data) >= f.size and not target:
            data = f.data  # nothing to gain: the original is already smaller
            saved = ctx.tr("уже оптимально", "already optimal")
        else:
            saved = f"{(1 - len(data) / max(1, f.size)) * 100:.0f}%"
        ctx.out_bytes(f"{f.stem}-small.{fmt}", data)
        rows.append([f.name, human_size(f.size, ctx.en), human_size(len(data), ctx.en), saved])
    out = {"table": table([ctx.tr("Файл", "File"), ctx.tr("Было", "Before"), ctx.tr("Стало", "After"), ctx.tr("Экономия", "Saved")], rows)}
    if notes:
        out["note"] = " · ".join(sorted(notes))
    return out

@utility("image.strip", "image", "Удалить метаданные (EXIF, GPS)", "Remove metadata (EXIF, GPS)",
         "Убирает из фото координаты, модель телефона, дату и прочие скрытые данные перед публикацией.",
         "Removes GPS coordinates, camera model, dates and other hidden data before you share a photo.",
         input="files", accept=IMG_ACCEPT, opts=[O("quality", "int", "Качество JPG/WebP", "JPG/WebP quality", 95, min=1, max=100)],
         ai_default=True, keys="exif gps метаданные удалить приватность strip metadata")
def image_strip(ctx):
    Image, _ = _pil()
    for f in _each(ctx):
        img = open_image(f)
        clean = Image.new(img.mode, img.size)
        clean.paste(img)
        if img.mode == "P" and img.getpalette():
            clean.putpalette(img.getpalette())
        emit(ctx, f, clean, out_format(ctx, f), "-clean", int(ctx.o["quality"]))
    return {"note": ctx.tr("Метаданные удалены, ориентация применена к пикселям", "Metadata removed; orientation baked into the pixels")}

def _gps(exif) -> Tuple[float, float]:
    try:
        gps = exif.get_ifd(0x8825)
    except Exception:  # noqa: BLE001
        return None
    if not gps or 2 not in gps or 4 not in gps:
        return None
    def deg(v):
        d, m, s = (float(x) for x in v)
        return d + m / 60 + s / 3600
    lat, lon = deg(gps[2]), deg(gps[4])
    if gps.get(1) == "S":
        lat = -lat
    if gps.get(3) == "W":
        lon = -lon
    return lat, lon

@utility("image.info", "image", "Информация и EXIF", "Image info and EXIF",
         "Размер, формат, цветовой режим, DPI, прозрачность, камера, объектив, выдержка, дата съёмки и GPS со ссылкой на карту.",
         "Size, format, color mode, DPI, transparency, camera, lens, exposure, date taken and GPS with a map link.",
         input="file", accept=IMG_ACCEPT, ai_default=True, keys="exif информация фото камера gps размер dpi info")
def image_info(ctx):
    from PIL import ExifTags
    f = ctx.file()
    Image, _ = _pil()
    img = Image.open(io.BytesIO(f.data))
    info = [[ctx.tr("Файл", "File"), f"{f.name} ({human_size(f.size, ctx.en)})"], [ctx.tr("Формат", "Format"), img.format or "—"],
            [ctx.tr("Размер", "Size"), f"{img.width} × {img.height} px ({img.width * img.height / 1e6:.1f} Мп)" if not ctx.en else f"{img.width} × {img.height} px ({img.width * img.height / 1e6:.1f} MP)"],
            [ctx.tr("Режим", "Mode"), img.mode], [ctx.tr("Прозрачность", "Transparency"), ctx.tr("есть", "yes") if img.mode in ("RGBA", "LA") or "transparency" in img.info else ctx.tr("нет", "no")]]
    if img.info.get("dpi"):
        info.append(["DPI", " × ".join(str(round(float(v))) for v in img.info["dpi"])])
    if getattr(img, "n_frames", 1) > 1:
        info.append([ctx.tr("Кадров", "Frames"), img.n_frames])
    exif = img.getexif()
    names = {0x010F: ("Производитель", "Make"), 0x0110: ("Камера", "Camera"), 0x0132: ("Дата", "Date"), 0x0131: ("Программа", "Software"),
             0x0112: ("Ориентация", "Orientation")}
    for tag, (ru, en) in names.items():
        if exif.get(tag):
            info.append([ctx.tr(ru, en), str(exif.get(tag)).strip("\x00 ")])
    try:
        sub = exif.get_ifd(0x8769)
        for tag, (ru, en) in {0x829A: ("Выдержка", "Exposure"), 0x829D: ("Диафрагма", "F-number"), 0x8827: ("ISO", "ISO"),
                              0x920A: ("Фокусное расстояние", "Focal length"), 0xA434: ("Объектив", "Lens"), 0x9003: ("Снято", "Taken")}.items():
            if sub.get(tag) is not None:
                v = sub.get(tag)
                if tag == 0x829A and float(v) < 1:
                    v = f"1/{round(1 / float(v))} s"
                elif tag == 0x829D:
                    v = f"f/{float(v):.1f}"
                elif tag == 0x920A:
                    v = f"{float(v):.0f} mm"
                info.append([ctx.tr(ru, en), str(v)])
    except Exception:  # noqa: BLE001
        pass
    gps = _gps(exif)
    if gps:
        info.append(["GPS", f"{gps[0]:.6f}, {gps[1]:.6f} — https://www.openstreetmap.org/?mlat={gps[0]:.6f}&mlon={gps[1]:.6f}#map=16/{gps[0]:.5f}/{gps[1]:.5f}"])
    extra = len(exif) - sum(1 for t in names if exif.get(t))
    if extra > 0:
        info.append([ctx.tr("Других EXIF-тегов", "Other EXIF tags"), extra])
    return {"info": info, "note": ctx.tr("⚠ В фото есть координаты съёмки — удалите их утилитой «Удалить метаданные» перед публикацией", "⚠ The photo contains GPS — remove it with “Remove metadata” before sharing") if gps else ""}

@utility("image.filters", "image", "Фильтры и коррекция", "Filters and adjustments",
         "Ч/б, сепия, негатив, размытие, резкость, яркость, контраст, насыщенность, пикселизация, постеризация, контуры.",
         "B&W, sepia, invert, blur, sharpen, brightness, contrast, saturation, pixelate, posterize, edges.",
         input="files", accept=IMG_ACCEPT,
         opts=[O("filter", "choice", "Фильтр", "Filter", "gray", choices=[("gray", "Чёрно-белое", "Grayscale"), ("sepia", "Сепия", "Sepia"), ("invert", "Негатив", "Invert"),
                                                                       ("blur", "Размытие", "Blur"), ("sharpen", "Резкость", "Sharpen"), ("bright", "Яркость", "Brightness"),
                                                                       ("contrast", "Контраст", "Contrast"), ("saturate", "Насыщенность", "Saturation"), ("pixelate", "Пикселизация", "Pixelate"),
                                                                       ("posterize", "Постеризация", "Posterize"), ("edges", "Контуры", "Edges"), ("emboss", "Тиснение", "Emboss"),
                                                                       ("autocontrast", "Автоконтраст", "Auto contrast"), ("equalize", "Выровнять гистограмму", "Equalize")]),
               O("amount", "number", "Сила", "Amount", 1.5, min=0, max=50, show="filter=blur|sharpen|bright|contrast|saturate|pixelate|posterize",
                 hint="Яркость/контраст/насыщенность: 1 — без изменений", hint_en="Brightness/contrast/saturation: 1 = unchanged"), _same_opt()],
         ai_default=True, keys="фильтр черно-белое сепия размытие резкость яркость контраст filter grayscale blur")
def image_filters(ctx):
    from PIL import ImageEnhance, ImageFilter, ImageOps
    Image, _ = _pil()
    for f in _each(ctx):
        img = open_image(f)
        alpha = img.getchannel("A") if img.mode == "RGBA" else None
        rgb = img.convert("RGB")
        k, flt = float(ctx.o["amount"]), ctx.o["filter"]
        if flt == "gray":
            rgb = ImageOps.grayscale(rgb).convert("RGB")
        elif flt == "sepia":
            g = ImageOps.grayscale(rgb)
            rgb = ImageOps.colorize(g, (40, 26, 13), (255, 240, 192), mid=(160, 120, 80))
        elif flt == "invert":
            rgb = ImageOps.invert(rgb)
        elif flt == "blur":
            rgb = rgb.filter(ImageFilter.GaussianBlur(k * 2))
        elif flt == "sharpen":
            rgb = rgb.filter(ImageFilter.UnsharpMask(radius=2, percent=int(80 * k), threshold=2))
        elif flt in ("bright", "contrast", "saturate"):
            rgb = {"bright": ImageEnhance.Brightness, "contrast": ImageEnhance.Contrast, "saturate": ImageEnhance.Color}[flt](rgb).enhance(k)
        elif flt == "pixelate":
            block = max(2, int(k * 8))
            small = rgb.resize((max(1, rgb.width // block), max(1, rgb.height // block)), Image.NEAREST)
            rgb = small.resize(rgb.size, Image.NEAREST)
        elif flt == "posterize":
            rgb = ImageOps.posterize(rgb, max(1, min(8, int(k * 2))))
        elif flt == "edges":
            rgb = rgb.filter(ImageFilter.FIND_EDGES)
        elif flt == "emboss":
            rgb = rgb.filter(ImageFilter.EMBOSS)
        elif flt == "autocontrast":
            rgb = ImageOps.autocontrast(rgb, cutoff=1)
        elif flt == "equalize":
            rgb = ImageOps.equalize(rgb)
        if alpha is not None:
            rgb = rgb.convert("RGBA")
            rgb.putalpha(alpha)
        emit(ctx, f, rgb, out_format(ctx, f), f"-{flt}")
    return ""

_POS = [("br", "Справа внизу", "Bottom right"), ("bl", "Слева внизу", "Bottom left"), ("tr", "Справа вверху", "Top right"), ("tl", "Слева вверху", "Top left"),
        ("c", "По центру", "Center"), ("tile", "Сеткой по всему фото", "Tiled across")]

@utility("image.watermark", "image", "Водяной знак", "Watermark",
         "Текстовый знак (© Имя, сайт) в углу, по центру или сеткой; прозрачность, размер, цвет. Второй файл — логотип вместо текста.",
         "A text mark (© Name, site) in a corner, center or tiled; opacity, size, color. A second file = a logo instead of text.",
         input="files", accept=IMG_ACCEPT,
         opts=[O("text", "text", "Текст", "Text", "© MemuAI"), O("pos", "choice", "Где", "Position", "br", choices=_POS),
               O("opacity", "int", "Непрозрачность, %", "Opacity, %", 45, min=1, max=100), O("size", "int", "Размер, % от ширины", "Size, % of width", 4, min=1, max=50),
               O("color", "color", "Цвет", "Color", "#ffffff"), O("logo", "bool", "Последний файл — логотип", "The last file is a logo", False), _same_opt()],
         ai_default=True, keys="водяной знак watermark подпись логотип копирайт защита фото")
def image_watermark(ctx):
    Image, _ = _pil()
    from PIL import ImageDraw
    files = list(_each(ctx))
    logo = None
    if ctx.o["logo"]:
        if len(files) < 2:
            raise UtilError("Добавьте фото и последним — логотип", "Add photos and the logo as the last file")
        logo = open_image(files.pop()).convert("RGBA")
    alpha = int(255 * int(ctx.o["opacity"]) / 100)
    color = tuple(parse_color(ctx.o["color"], (255, 255, 255))[:3])
    for f in files:
        img = open_image(f).convert("RGBA")
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        target_w = max(8, img.width * int(ctx.o["size"]) / 100 * (4 if logo else 1))
        if logo:
            k = target_w / logo.width
            mark = logo.resize((max(1, int(logo.width * k)), max(1, int(logo.height * k))), Image.LANCZOS)
            a = mark.getchannel("A").point(lambda v: v * alpha // 255)
            mark.putalpha(a)
        else:
            fnt = font(max(10, int(img.width * int(ctx.o["size"]) / 100)), True)
            d0 = ImageDraw.Draw(layer)
            box = d0.textbbox((0, 0), ctx.o["text"], font=fnt)
            mark = Image.new("RGBA", (box[2] - box[0] + 8, box[3] - box[1] + 8), (0, 0, 0, 0))
            dm = ImageDraw.Draw(mark)
            dm.text((4 - box[0] + 1, 4 - box[1] + 1), ctx.o["text"], font=fnt, fill=(0, 0, 0, alpha // 2))
            dm.text((4 - box[0], 4 - box[1]), ctx.o["text"], font=fnt, fill=color + (alpha,))
        pad = int(min(img.size) * 0.03)
        pos = ctx.o["pos"]
        if pos == "tile":
            tile = mark.rotate(30, expand=True)
            for y in range(0, img.height, tile.height + pad * 3):
                for x in range(0 - (y // 3) % (tile.width + pad), img.width, tile.width + pad * 3):
                    layer.alpha_composite(tile, (max(0, x), y)) if x >= 0 else None
        else:
            x = {"br": img.width - mark.width - pad, "tr": img.width - mark.width - pad, "bl": pad, "tl": pad, "c": (img.width - mark.width) // 2}[pos]
            y = {"br": img.height - mark.height - pad, "bl": img.height - mark.height - pad, "tr": pad, "tl": pad, "c": (img.height - mark.height) // 2}[pos]
            layer.alpha_composite(mark, (max(0, x), max(0, y)))
        out = Image.alpha_composite(img, layer)
        emit(ctx, f, out, out_format(ctx, f), "-wm")
    return ""

@utility("image.frame", "image", "Рамка, скругление, тень", "Border, rounded corners, shadow",
         "Добавляет рамку и поля, скругляет углы (PNG с прозрачностью), делает мягкую тень — для скриншотов и карточек.",
         "Adds a border and padding, rounds corners (transparent PNG), drops a soft shadow — for screenshots and cards.",
         input="files", accept=IMG_ACCEPT,
         opts=[O("radius", "int", "Скругление, px", "Corner radius, px", 24, min=0, max=2000), O("border", "int", "Рамка, px", "Border, px", 0, min=0, max=500),
               O("bcolor", "color", "Цвет рамки", "Border color", "#ffffff"), O("pad", "int", "Поля вокруг, px", "Padding, px", 0, min=0, max=1000),
               O("bg", "color", "Цвет полей (пусто — прозрачные)", "Padding color (empty = transparent)", ""), O("shadow", "bool", "Мягкая тень", "Soft shadow", False)],
         keys="рамка скругление углы тень border rounded corners shadow скриншот")
def image_frame(ctx):
    Image, _ = _pil()
    from PIL import ImageDraw, ImageFilter
    for f in _each(ctx):
        img = open_image(f).convert("RGBA")
        b = int(ctx.o["border"])
        if b:
            framed = Image.new("RGBA", (img.width + 2 * b, img.height + 2 * b), tuple(parse_color(ctx.o["bcolor"], (255, 255, 255))[:3]) + (255,))
            framed.alpha_composite(img, (b, b))
            img = framed
        r = int(ctx.o["radius"])
        if r:
            mask = Image.new("L", img.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=r, fill=255)
            img.putalpha(Image.composite(img.getchannel("A"), Image.new("L", img.size, 0), mask))
        pad = int(ctx.o["pad"]) + (int(min(img.size) * 0.06) if ctx.o["shadow"] else 0)
        if pad:
            bg = parse_color(ctx.o["bg"], None) if str(ctx.o["bg"]).strip() else None
            canvas = Image.new("RGBA", (img.width + 2 * pad, img.height + 2 * pad), (tuple(bg[:3]) + (255,)) if bg else (0, 0, 0, 0))
            if ctx.o["shadow"]:
                sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
                shape = Image.new("RGBA", img.size, (0, 0, 0, 110))
                shape.putalpha(img.getchannel("A").point(lambda v: min(v, 110)))
                sh.alpha_composite(shape, (pad, pad + pad // 4))
                sh = sh.filter(ImageFilter.GaussianBlur(pad / 3))
                canvas.alpha_composite(sh)
            canvas.alpha_composite(img, (pad, pad))
            img = canvas
        ctx.out_bytes(f"{f.stem}-framed.png", save_image(img, "png"))
    return ""

@utility("image.trim", "image", "Обрезать пустые поля", "Trim empty margins",
         "Автоматически срезает однотонные или прозрачные поля вокруг картинки (сканы, логотипы, скриншоты).",
         "Automatically trims solid or transparent margins around an image (scans, logos, screenshots).",
         input="files", accept=IMG_ACCEPT, opts=[O("tolerance", "int", "Допуск цвета", "Color tolerance", 12, min=0, max=200),
                                                 O("margin", "int", "Оставить поля, px", "Keep margin, px", 0, min=0, max=500), _same_opt()],
         keys="обрезать поля trim autocrop пустые края")
def image_trim(ctx):
    Image, _ = _pil()
    from PIL import ImageChops
    for f in _each(ctx):
        img = open_image(f)
        if img.mode in ("RGBA", "LA") and img.getchannel("A").getextrema()[0] < 250:
            box = img.getchannel("A").point(lambda v: 255 if v > int(ctx.o["tolerance"]) else 0).getbbox()
        else:
            rgb = img.convert("RGB")
            bg = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
            diff = ImageChops.difference(rgb, bg).convert("L").point(lambda v: 255 if v > int(ctx.o["tolerance"]) else 0)
            box = diff.getbbox()
        if not box:
            raise UtilError("Изображение однотонное — обрезать нечего", "The image is a single color — nothing to trim")
        m = int(ctx.o["margin"])
        box = (max(0, box[0] - m), max(0, box[1] - m), min(img.width, box[2] + m), min(img.height, box[3] + m))
        emit(ctx, f, img.crop(box), out_format(ctx, f), "-trim")
    return ""

@utility("image.favicon", "image", "Favicon и иконки приложения", "Favicon and app icons",
         "Из одной картинки: favicon.ico (16–256), PNG для Apple/Android, site.webmanifest и HTML-код — одним ZIP.",
         "From one picture: favicon.ico (16–256), PNGs for Apple/Android, site.webmanifest and the HTML snippet — in one ZIP.",
         input="file", accept=IMG_ACCEPT, opts=[O("bg", "color", "Фон (пусто — прозрачный)", "Background (empty = transparent)", ""),
                                                O("pad", "int", "Отступ, %", "Padding, %", 0, min=0, max=40)],
         ai_default=True, keys="favicon фавикон иконка ico сайт apple touch icon")
def image_favicon(ctx):
    Image, ImageOps = _pil()
    f = ctx.file()
    img = open_image(f).convert("RGBA")
    side = max(img.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.alpha_composite(img, ((side - img.width) // 2, (side - img.height) // 2))
    pad = int(side * int(ctx.o["pad"]) / 100)
    if pad:
        canvas = Image.new("RGBA", (side + 2 * pad, side + 2 * pad), (0, 0, 0, 0))
        canvas.alpha_composite(square, (pad, pad))
        square = canvas
    if str(ctx.o["bg"]).strip():
        bg = Image.new("RGBA", square.size, tuple(parse_color(ctx.o["bg"], (255, 255, 255))[:3]) + (255,))
        bg.alpha_composite(square)
        square = bg
    files: Dict[str, bytes] = {}
    buf = io.BytesIO()
    square.save(buf, "ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    files["favicon.ico"] = buf.getvalue()
    for name, size in (("favicon-16x16.png", 16), ("favicon-32x32.png", 32), ("apple-touch-icon.png", 180), ("android-chrome-192x192.png", 192),
                       ("android-chrome-512x512.png", 512), ("mstile-150x150.png", 150)):
        files[name] = save_image(square.resize((size, size), Image.LANCZOS), "png")
    files["site.webmanifest"] = ('{\n  "name": "",\n  "short_name": "",\n  "icons": [\n    {"src": "/android-chrome-192x192.png", "sizes": "192x192", "type": "image/png"},\n'
                                 '    {"src": "/android-chrome-512x512.png", "sizes": "512x512", "type": "image/png"}\n  ],\n  "theme_color": "#ffffff",\n'
                                 '  "background_color": "#ffffff",\n  "display": "standalone"\n}\n').encode()
    html = ('<link rel="icon" href="/favicon.ico" sizes="any">\n<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">\n'
            '<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">\n<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">\n'
            '<link rel="manifest" href="/site.webmanifest">')
    files["head.html"] = html.encode()
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    ctx.out_bytes("favicon-package.zip", zbuf.getvalue())
    ctx.out_bytes("favicon.ico", files["favicon.ico"])
    ctx.out_bytes("apple-touch-icon.png", files["apple-touch-icon.png"])
    return {"text": html, "lang": "html"}

@utility("image.colors", "image", "Основные цвета картинки", "Dominant colors",
         "Извлекает палитру из фото или логотипа: HEX-коды основных цветов с долями.", "Extracts a palette from a photo or logo: HEX codes with shares.",
         input="file", accept=IMG_ACCEPT, opts=[O("count", "int", "Цветов", "Colors", 6, min=2, max=16)], ai_default=True,
         keys="цвета палитра из картинки dominant colors palette extract")
def image_colors(ctx):
    Image, _ = _pil()
    from .colors import to_hex
    img = open_image(ctx.file()).convert("RGB")
    img.thumbnail((200, 200))
    q = img.quantize(int(ctx.o["count"]), method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[: int(ctx.o["count"]) * 3]
    counts = sorted(q.getcolors(), reverse=True)
    total = sum(c for c, _ in counts)
    rows, swatches = [], []
    for c, idx in counts:
        rgb = tuple(pal[idx * 3: idx * 3 + 3])
        hexv = to_hex(rgb)
        swatches.append(hexv)
        rows.append([hexv, f"rgb{rgb}", f"{c * 100 / total:.1f}%"])
    from .t_check import _swatch_png
    ctx.out_bytes("palette.png", _swatch_png([tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) for h in swatches], 100))
    return {"table": table(["HEX", "RGB", ctx.tr("Доля", "Share")], rows), "swatches": swatches}

@utility("image.collage", "image", "Коллаж из фото", "Photo collage",
         "Собирает несколько фото в сетку с отступами и фоном — для сравнения «до/после», сторис и отчётов.",
         "Arranges several photos in a grid with gaps and a background — for before/after, stories and reports.",
         input="files", accept=IMG_ACCEPT,
         opts=[O("cols", "int", "Столбцов (0 — авто)", "Columns (0 = auto)", 0, min=0, max=20), O("cell", "int", "Размер ячейки, px", "Cell size, px", 600, min=50, max=4000),
               O("gap", "int", "Отступ, px", "Gap, px", 12, min=0, max=200), O("bg", "color", "Фон", "Background", "#ffffff"),
               O("fit", "choice", "Заполнение ячейки", "Cell fill", "cover", choices=[("cover", "Обрезать по ячейке", "Crop to cell"), ("contain", "Вписать целиком", "Fit whole")]),
               O("format", "choice", "Формат", "Format", "jpg", choices=[("jpg", "JPG", "JPG"), ("png", "PNG", "PNG"), ("webp", "WebP", "WebP")])],
         keys="коллаж сетка фото collage grid до после")
def image_collage(ctx):
    Image, ImageOps = _pil()
    files = _each(ctx)
    n = len(files)
    cols = int(ctx.o["cols"]) or math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    cell, gap = int(ctx.o["cell"]), int(ctx.o["gap"])
    bg = tuple(parse_color(ctx.o["bg"], (255, 255, 255))[:3])
    canvas = Image.new("RGB", (cols * cell + (cols + 1) * gap, rows * cell + (rows + 1) * gap), bg)
    for i, f in enumerate(files):
        img = open_image(f).convert("RGB")
        if ctx.o["fit"] == "cover":
            tile = ImageOps.fit(img, (cell, cell), Image.LANCZOS)
            offset = (0, 0)
        else:
            img.thumbnail((cell, cell), Image.LANCZOS)
            tile, offset = img, ((cell - img.width) // 2, (cell - img.height) // 2)
        r, c = divmod(i, cols)
        canvas.paste(tile, (gap + c * (cell + gap) + offset[0], gap + r * (cell + gap) + offset[1]))
    ctx.out_bytes(f"collage.{ctx.o['format']}", save_image(canvas, ctx.o["format"], 90))
    return {"note": f"{cols}×{rows}"}

@utility("image.gif", "image", "GIF/WebP-анимация из картинок", "GIF/WebP animation from images",
         "Склеивает кадры в анимированный GIF или WebP с нужной скоростью и повтором.", "Joins frames into an animated GIF or WebP with your speed and looping.",
         input="files", accept=IMG_ACCEPT,
         opts=[O("delay", "int", "Задержка кадра, мс", "Frame delay, ms", 500, min=20, max=10000), O("format", "choice", "Формат", "Format", "gif", choices=[("gif", "GIF", "GIF"), ("webp", "WebP", "WebP")]),
               O("width", "int", "Ширина, px (0 — как у первого)", "Width, px (0 = as first)", 600, min=0, max=4000), O("loop", "bool", "Повторять бесконечно", "Loop forever", True),
               O("bounce", "bool", "Туда-обратно", "Ping-pong", False)],
         keys="gif анимация гифка webp анимированный кадры animation")
def image_gif(ctx):
    Image, ImageOps = _pil()
    files = _each(ctx)
    if len(files) < 2:
        raise UtilError("Нужно хотя бы два кадра", "Need at least two frames")
    first = open_image(files[0])
    w = int(ctx.o["width"]) or first.width
    size = (w, round(first.height * w / first.width))
    frames = [ImageOps.fit(open_image(f).convert("RGBA"), size, Image.LANCZOS) for f in files]
    if ctx.o["bounce"]:
        frames += frames[-2:0:-1]
    buf = io.BytesIO()
    kw = {"save_all": True, "append_images": frames[1:], "duration": int(ctx.o["delay"]), "loop": 0 if ctx.o["loop"] else 1}
    if ctx.o["format"] == "gif":
        pal = [fr.convert("RGB").quantize(256, method=Image.Quantize.MEDIANCUT) for fr in frames]
        pal[0].save(buf, "GIF", **{**kw, "append_images": pal[1:]}, disposal=2, optimize=True)
    else:
        frames[0].save(buf, "WEBP", quality=85, **kw)
    ctx.out_bytes(f"animation.{ctx.o['format']}", buf.getvalue())
    return {"note": ctx.tr(f"Кадров: {len(frames)}, {size[0]}×{size[1]}", f"Frames: {len(frames)}, {size[0]}×{size[1]}")}

@utility("image.frames", "image", "Кадры из GIF/WebP", "Frames from GIF/WebP",
         "Раскладывает анимированный GIF/WebP/APNG на отдельные PNG (в ZIP) и показывает задержки кадров.",
         "Splits an animated GIF/WebP/APNG into separate PNGs (in a ZIP) and shows frame delays.",
         input="file", accept=".gif,.webp,.png,.apng", keys="кадры gif разобрать frames extract анимация")
def image_frames(ctx):
    from PIL import ImageSequence
    f = ctx.file()
    img = open_image(f, exif_turn=False)
    zbuf = io.BytesIO()
    count, total = 0, 0
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, frame in enumerate(ImageSequence.Iterator(img)):
            z.writestr(f"{f.stem}-{i + 1:03d}.png", save_image(frame.convert("RGBA"), "png"))
            count += 1
            total += frame.info.get("duration", 0) or 0
            if count >= 2000:
                break
    ctx.out_bytes(f"{f.stem}-frames.zip", zbuf.getvalue())
    return {"note": ctx.tr(f"Кадров: {count}, длительность {total / 1000:.2f} с", f"Frames: {count}, duration {total / 1000:.2f} s")}

@utility("image.ascii", "image", "Картинка → ASCII-арт", "Image → ASCII art",
         "Превращает изображение в текстовую картинку из символов (для README, чатов, терминала).", "Turns an image into text art (for READMEs, chats, terminals).",
         input="file", accept=IMG_ACCEPT, opts=[O("width", "int", "Ширина, символов", "Width, characters", 80, min=10, max=400),
                                                O("charset", "choice", "Символы", "Characters", "classic", choices=[("classic", "@%#*+=-:. ", "@%#*+=-:. "), ("blocks", "█▓▒░ ", "█▓▒░ "), ("dense", "Плотный набор", "Dense set")]),
                                                O("invert", "bool", "Инвертировать (для тёмного фона)", "Invert (for dark backgrounds)", False)],
         keys="ascii арт текст картинка символы ascii art")
def image_ascii(ctx):
    img = open_image(ctx.file()).convert("L")
    w = int(ctx.o["width"])
    h = max(1, round(img.height / img.width * w * 0.5))
    img = img.resize((w, h))
    chars = {"classic": "@%#*+=-:. ", "blocks": "█▓▒░ ", "dense": "$@B%8&WM#*oahkbdpqwmZO0QLCJUYXzcvunxrjft/\\|()1{}[]?-_+~<>i!lI;:,\"^`'. "}[ctx.o["charset"]]
    if ctx.o["invert"]:
        chars = chars[::-1]
    px = img.convert("L").tobytes()
    rows = ["".join(chars[min(len(chars) - 1, p * len(chars) // 256)] for p in px[r * w:(r + 1) * w]) for r in range(h)]
    return {"text": "\n".join(rows), "mono": True}

@utility("image.transparent", "image", "Убрать фон (однотонный)", "Remove a solid background",
         "Делает прозрачным белый (или любой) фон — для логотипов, подписей и печатей. Сложные фото не для этого.",
         "Makes a white (or any solid) background transparent — for logos, signatures and stamps. Not for complex photos.",
         input="files", accept=IMG_ACCEPT,
         opts=[O("color", "color", "Цвет фона (пусто — из угла)", "Background color (empty = corner pixel)", ""), O("tolerance", "int", "Допуск", "Tolerance", 40, min=0, max=255),
               O("soft", "bool", "Мягкие края", "Soft edges", True)],
         ai_default=True, keys="прозрачный фон убрать фон белый transparent background remove подпись печать логотип")
def image_transparent(ctx):
    for f in _each(ctx):
        img = open_image(f).convert("RGBA")
        key = parse_color(ctx.o["color"], None) if str(ctx.o["color"]).strip() else img.getpixel((0, 0))[:3]
        tol, soft = int(ctx.o["tolerance"]), ctx.o["soft"]
        px = img.load()
        for y in range(img.height):
            for x in range(img.width):
                r, g, b, a = px[x, y]
                d = max(abs(r - key[0]), abs(g - key[1]), abs(b - key[2]))
                if d <= tol:
                    px[x, y] = (r, g, b, 0)
                elif soft and d <= tol * 2:
                    px[x, y] = (r, g, b, min(a, int(255 * (d - tol) / max(1, tol))))
        ctx.out_bytes(f"{f.stem}-transparent.png", save_image(img, "png"))
    return ""

@utility("image.grid", "image", "Разрезать на части", "Split into tiles",
         "Режет картинку на сетку N×M (панорама для Instagram, пазлы, печать по частям) — ZIP с кусками.",
         "Cuts an image into an N×M grid (Instagram panoramas, puzzles, tiled printing) — a ZIP of tiles.",
         input="file", accept=IMG_ACCEPT, opts=[O("cols", "int", "Столбцов", "Columns", 3, min=1, max=50), O("rows", "int", "Строк", "Rows", 1, min=1, max=50), _same_opt()],
         keys="разрезать сетка части panorama split tiles instagram")
def image_grid(ctx):
    f = ctx.file()
    img = open_image(f)
    cols, rows = int(ctx.o["cols"]), int(ctx.o["rows"])
    fmt = out_format(ctx, f)
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in range(rows):
            for c in range(cols):
                box = (img.width * c // cols, img.height * r // rows, img.width * (c + 1) // cols, img.height * (r + 1) // rows)
                z.writestr(f"{f.stem}-r{r + 1}-c{c + 1}.{fmt}", save_image(img.crop(box), fmt))
    ctx.out_bytes(f"{f.stem}-tiles.zip", zbuf.getvalue())
    return {"note": ctx.tr(f"Частей: {cols * rows}", f"Tiles: {cols * rows}")}

@utility("image.avatar", "image", "Круглая аватарка", "Round avatar",
         "Вырезает круг или квадрат со скруглением из центра фото нужного размера — PNG с прозрачностью.",
         "Cuts a circle or rounded square from the photo center at any size — a transparent PNG.",
         input="files", accept=IMG_ACCEPT, opts=[O("size", "int", "Размер, px", "Size, px", 512, min=16, max=4096),
                                                 O("shape", "choice", "Форма", "Shape", "circle", choices=[("circle", "Круг", "Circle"), ("squircle", "Скруглённый квадрат", "Rounded square")]),
                                                 O("ring", "int", "Обводка, px", "Ring, px", 0, min=0, max=200), O("rcolor", "color", "Цвет обводки", "Ring color", "#ffffff")],
         keys="аватар аватарка круг круглое фото avatar circle profile")
def image_avatar(ctx):
    Image, ImageOps = _pil()
    from PIL import ImageDraw
    for f in _each(ctx):
        size = int(ctx.o["size"])
        img = ImageOps.fit(open_image(f).convert("RGBA"), (size * 2, size * 2), Image.LANCZOS)
        mask = Image.new("L", img.size, 0)
        d = ImageDraw.Draw(mask)
        if ctx.o["shape"] == "circle":
            d.ellipse([0, 0, img.width - 1, img.height - 1], fill=255)
        else:
            d.rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=img.width // 4, fill=255)
        img.putalpha(mask)
        ring = int(ctx.o["ring"]) * 2
        if ring:
            dr = ImageDraw.Draw(img)
            col = tuple(parse_color(ctx.o["rcolor"], (255, 255, 255))[:3]) + (255,)
            if ctx.o["shape"] == "circle":
                dr.ellipse([ring // 2, ring // 2, img.width - 1 - ring // 2, img.height - 1 - ring // 2], outline=col, width=ring)
            else:
                dr.rounded_rectangle([ring // 2, ring // 2, img.width - 1 - ring // 2, img.height - 1 - ring // 2], radius=img.width // 4, outline=col, width=ring)
        img = img.resize((size, size), Image.LANCZOS)
        ctx.out_bytes(f"{f.stem}-avatar.png", save_image(img, "png"))
    return ""

@utility("image.compare", "image", "Сравнить две картинки", "Compare two images",
         "Находит различия между двумя изображениями: процент отличий и карта изменений с подсветкой.",
         "Finds differences between two images: the percentage and a highlighted difference map.",
         input="files", accept=IMG_ACCEPT, opts=[O("threshold", "int", "Порог чувствительности", "Sensitivity threshold", 24, min=0, max=255)],
         keys="сравнить картинки разница отличия diff compare images")
def image_compare(ctx):
    Image, _ = _pil()
    from PIL import ImageChops
    if len(ctx.files) < 2:
        raise UtilError("Добавьте два изображения", "Add two images")
    a = open_image(ctx.files[0]).convert("RGB")
    b = open_image(ctx.files[1]).convert("RGB")
    if a.size != b.size:
        b = b.resize(a.size, Image.LANCZOS)
    diff = ImageChops.difference(a, b).convert("L")
    mask = diff.point(lambda v: 255 if v > int(ctx.o["threshold"]) else 0)
    changed = mask.histogram()[255]
    share = changed / (a.width * a.height) * 100
    overlay = a.convert("RGBA")
    red = Image.new("RGBA", a.size, (255, 0, 60, 150))
    overlay.paste(red, (0, 0), mask)
    ctx.out_bytes("difference.png", save_image(overlay, "png"))
    box = mask.getbbox()
    return {"info": [[ctx.tr("Отличается пикселей", "Changed pixels"), f"{changed} ({share:.2f}%)"],
                     [ctx.tr("Область изменений", "Changed area"), str(box) if box else ctx.tr("нет", "none")],
                     [ctx.tr("Итог", "Verdict"), ctx.tr("одинаковые", "identical") if not changed else ctx.tr("есть различия", "different")]]}

@utility("image.meme", "image", "Мем: текст сверху и снизу", "Meme: top and bottom text",
         "Классический мем — крупный белый текст с чёрной обводкой сверху и снизу картинки.", "The classic meme — big white outlined text on top and bottom.",
         input="file", accept=IMG_ACCEPT, opts=[O("top", "text", "Текст сверху", "Top text", ""), O("bottom", "text", "Текст снизу", "Bottom text", "")],
         keys="мем meme текст на картинке подпись")
def image_meme(ctx):
    from PIL import ImageDraw
    f = ctx.file()
    img = open_image(f).convert("RGB")
    d = ImageDraw.Draw(img)
    for text, top in ((ctx.o["top"], True), (ctx.o["bottom"], False)):
        if not text:
            continue
        text = text.upper()
        size = max(12, img.width // 9)
        fnt = font(size, True)
        while size > 12 and d.textbbox((0, 0), text, font=fnt)[2] > img.width * 0.94:
            size -= 2
            fnt = font(size, True)
        box = d.textbbox((0, 0), text, font=fnt)
        x = (img.width - (box[2] - box[0])) / 2 - box[0]
        y = img.height * 0.03 - box[1] if top else img.height * 0.97 - (box[3] - box[1]) - box[1]
        d.text((x, y), text, font=fnt, fill="white", stroke_width=max(2, size // 14), stroke_fill="black")
    if not ctx.o["top"] and not ctx.o["bottom"]:
        raise UtilError("Введите текст сверху или снизу", "Enter top or bottom text")
    ctx.out_bytes(f"{f.stem}-meme.jpg", save_image(img, "jpg", 92))
    return ""

@utility("image.to_pdf", "image", "Картинки → PDF", "Images → PDF",
         "Собирает фото и сканы в один PDF: по странице на картинку, A4 или по размеру фото, с полями.",
         "Combines photos and scans into one PDF: a page per image, A4 or image-sized, with margins.",
         input="files", accept=IMG_ACCEPT,
         opts=[O("page", "choice", "Страница", "Page", "a4", choices=[("a4", "A4 (вписать)", "A4 (fit)"), ("fit", "По размеру картинки", "Image size"), ("letter", "Letter", "Letter")]),
               O("margin", "int", "Поля, мм", "Margins, mm", 10, min=0, max=60, show="page=a4|letter"), O("quality", "int", "Качество JPEG", "JPEG quality", 85, min=10, max=100)],
         ai_default=True, keys="картинки в pdf фото в pdf сканы jpg to pdf images to pdf")
def image_to_pdf(ctx):
    Image, _ = _pil()
    pages = []
    for f in _each(ctx):
        img = open_image(f)
        if img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            rgba = img.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[3])
            img = bg
        img = img.convert("RGB")
        if ctx.o["page"] != "fit":
            pw, ph = (595, 842) if ctx.o["page"] == "a4" else (612, 792)
            if img.width > img.height:
                pw, ph = ph, pw
            dpi = 150
            W, H = pw * dpi // 72, ph * dpi // 72
            m = int(ctx.o["margin"]) * dpi / 25.4
            k = min((W - 2 * m) / img.width, (H - 2 * m) / img.height, 1.0 if img.width > W else (W - 2 * m) / img.width)
            k = min(k, (W - 2 * m) / img.width, (H - 2 * m) / img.height)
            scaled = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)
            page = Image.new("RGB", (W, H), (255, 255, 255))
            page.paste(scaled, ((W - scaled.width) // 2, (H - scaled.height) // 2))
            page.info["dpi"] = (dpi, dpi)
            pages.append(page)
        else:
            pages.append(img)
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:], resolution=150, quality=int(ctx.o["quality"]))
    ctx.out_bytes("images.pdf", buf.getvalue())
    return {"note": ctx.tr(f"Страниц: {len(pages)}", f"Pages: {len(pages)}")}
