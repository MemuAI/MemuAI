"""Local, bounded file inspection for descriptive searches."""
from __future__ import annotations

import colorsys
import html
import os
import re
import stat
import zipfile
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .fsindex_query import KINDS

TEXT = set(KINDS["code"]) | {"txt", "md", "markdown", "csv", "tsv", "log", "cfg", "conf", "rst",
                            "ini", "tex", "srt", "vtt", "rtf", "eml", "fb2"}
OFFICE_PART = re.compile(r"(?:word/document|content|xl/(?:sharedStrings|worksheets/sheet\d+)|ppt/slides/slide\d+)\.xml$")
OFFICE_BLOCK = re.compile(r"</(?:w:p|a:p|si|row)>")
OFFICE_RUN = re.compile(r"<(?:w:t|a:t|t|v)(?:\s[^>]*)?>(.*?)</(?:w:t|a:t|t|v)>", re.S)
OFFICE = {"docx", "odt", "ods", "odp", "xlsx", "pptx", "epub"}
READABLE = TEXT | OFFICE | {"pdf"}
IMAGES = set(KINDS["image"])
MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
# FD-3: PDF text extraction had no char limit and no deadline — a huge PDF
# could block the indexer for minutes. Cap both (the 24-page cap stays).
MAX_PDF_CHARS = 200_000
PDF_EXTRACT_DEADLINE_S = 10.0

_TYPES = {
    **{word: "type:image" for word in "image images photo photos picture pictures фото картинка картинку картинки изображение изображения фотография фотографию фотографии".split()},
    **{word: "type:video" for word in "video videos movie movies видео ролик ролики".split()},
    **{word: "type:audio" for word in "audio song songs music аудио музыка песня песни".split()},
    **{word: "type:doc" for word in "document documents документ документы".split()},
    **{word: "type:archive" for word in "archive archives архив архивы".split()},
    **{word: "type:code" for word in "code код исходник исходники".split()},
    **{word: "folder:" for word in "folder folders папка папки".split()},
    **{word: "file:" for word in "file files файл файлы".split()},
    **{word: f"ext:{word.rstrip('s')}" for word in "pdf pdfs docx xlsx pptx txt png jpg jpeg webp mp4 mp3 zip".split()},
}
_COLOR_WORDS = {
    "black": r"black|ч[её]рн\w*", "white": r"white|бел\w*", "gray": r"gr[ae]y|сер\w*",
    "red": r"red|красн\w*", "orange": r"orange|оранжев\w*", "yellow": r"yellow|ж[её]лт\w*",
    "green": r"green|зел[её]н\w*", "cyan": r"cyan|teal|голуб\w*|бирюзов\w*", "blue": r"blue|син\w*",
    "purple": r"purple|violet|фиолетов\w*", "pink": r"pink|magenta|розов\w*", "brown": r"brown|коричнев\w*",
}
_SHAPES = {"portrait": r"portrait|vertical|вертикальн\w*", "landscape": r"landscape|horizontal|горизонтальн\w*",
           "square": r"square|квадратн\w*"}
_WHEN = {
    "today": r"today|сегодня", "yesterday": r"yesterday|вчера",
    "lastweek": r"last week|прошл(?:ую|ой|ая) недел(?:ю|е|я)",
    "thisweek": r"this week|эт(?:у|ой|а) недел(?:ю|е|я)",
    "lastmonth": r"last month|прошл(?:ый|ом|ого) месяц(?:е|а)?",
    "thismonth": r"this month|эт(?:от|ом|ого) месяц(?:е|а)?",
}
_TOKEN = re.compile(r'(?:[^\s"]|"[^"]*")+')


def _alias(value: str, aliases: dict[str, str]) -> str:
    return next((key for key, pattern in aliases.items() if re.fullmatch(pattern, value, re.I)), "")


def _text(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


@dataclass(frozen=True)
class Description:
    base: str
    content: str = ""
    color: str = ""
    shape: str = ""
    resolution: tuple[int, int] | None = None

    @property
    def inspect(self) -> bool:
        return bool(self.content or self.color or self.shape or self.resolution)

    @property
    def image(self) -> bool:
        return bool(self.color or self.shape or self.resolution)

    def matches(self, path: str) -> bool:
        ext = Path(path).suffix.lower().lstrip(".")
        if (self.image and ext not in IMAGES) or (self.content and ext not in READABLE):
            return False
        try:
            info = os.stat(path, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x1400:
                return False
            if self.image:
                width, height, colors = _image(path, info.st_size, info.st_mtime_ns, bool(self.color))
                if not width or not height or (self.resolution and self.resolution != (width, height)):
                    return False
                if self.shape and self.shape != ("square" if width == height else "landscape" if width > height else "portrait"):
                    return False
                if self.color and self.color not in colors:
                    return False
            return not self.content or (info.st_size <= MAX_TEXT_BYTES and _content(path, ext, self.content))
        except (OSError, ValueError):
            return False

    def index_base(self) -> str:
        base = self.base.strip()
        if self.image:
            return (base + " type:image").strip()
        if self.content:
            return (base + " ext:" + ";".join(sorted(READABLE))).strip()
        return base


def parse_description(text: str) -> Description:
    raw = text.strip()[:1000]
    terms = _TOKEN.findall(raw)
    kept = []
    content = color = shape = ""
    resolution = None
    for term in terms:
        key, sep, value = term.partition(":")
        value = value.strip('"')
        if sep and key.lower() in ("content", "text", "содержимое") and value:
            content = value
        elif sep and key.lower() in ("color", "colour", "цвет") and (found := _alias(value, _COLOR_WORDS)):
            color = found
        elif sep and key.lower() in ("shape", "orientation", "форма") and (found := _alias(value, _SHAPES)):
            shape = found
        elif sep and key.lower() in ("resolution", "разрешение") and (dims := re.fullmatch(r"(\d{1,5})[xх×](\d{1,5})", value, re.I)):
            resolution = (int(dims[1]), int(dims[2]))
        else:
            kept.append(term)
    kind_pos = next((i for i, word in enumerate(kept)
                     if word.casefold() in _TYPES or word.lower().startswith(("type:", "kind:", "ext:"))), None)
    natural = kind_pos is not None and (len(kept) > 1 or content or color or shape or resolution)
    if not natural and len(terms) == len(kept):
        return Description(raw)
    if natural:
        # A descriptor and a type together form a natural-language filter.
        # Without a descriptor the filename query retains its original words.
        original = kept
        candidate = original.copy()
        candidate[kind_pos] = _TYPES.get(candidate[kind_pos].casefold(), candidate[kind_pos])
        base = " ".join(candidate)
        base = re.sub(r"^(?:find(?: me)?(?: an?)?|show me(?: an?)?|найди(?:те)?|покажи(?:те)?)\s+", "", base, flags=re.I)
        metadata = False
        if not content:
            marker = re.search(r'(?<!\S)(?:with text|containing|about|с текстом|содержит|про|о)\s+(.+)$', base, re.I)
            if marker:
                content = marker[1].strip('"')
                base = base[:marker.start()].strip()
        for key, pattern in _WHEN.items():
            stamp = re.search(rf"(?<!\S)(?:(?:from|modified|за|от|в) )?(?:{pattern})(?!\S)", base, re.I)
            if stamp:
                base = base[:stamp.start()] + base[stamp.end():] + f" dm:{key}"
                metadata = True
                break
        span = re.search(r"(?<!\S)(?:from the last|from last|last|за последние)\s+(\d{1,3})\s+(?:days?|день|дня|дней)(?!\S)", base, re.I)
        if span and int(span[1]) > 0:
            base = base[:span.start()] + base[span.end():] + f" dm:{span[1]}d"
            metadata = True
        size = re.search(r"(?<!\S)(over|under|larger than|smaller than|больше|меньше)\s+(\d+(?:[.,]\d+)?)\s*(kb|mb|gb|кб|мб|гб)(?!\S)", base, re.I)
        if size:
            op = "<" if size[1].lower() in ("under", "smaller than", "меньше") else ">"
            base = base[:size.start()] + base[size.end():] + f" size:{op}{size[2]}{size[3]}"
            metadata = True
        kept = []
        image_type = any(term in ("type:image", "kind:image") or term.startswith("ext:")
                         and any(ext.lstrip(".") in IMAGES for ext in re.split(r"[;,|]", term[4:]))
                         for term in base.lower().split())
        for term in _TOKEN.findall(base):
            if term.casefold() in ("big", "large", "большие", "крупные"):
                kept.append("size:>10mb")
                metadata = True
            elif term.casefold() in ("small", "маленькие", "небольшие"):
                kept.append("size:<1mb")
                metadata = True
            elif image_type and not color and (found := _alias(term, _COLOR_WORDS)):
                color = found
            elif image_type and not shape and (found := _alias(term, _SHAPES)):
                shape = found
            elif image_type and not resolution and (dims := re.fullmatch(r"(\d{1,5})[xх×](\d{1,5})", term, re.I)):
                resolution = (int(dims[1]), int(dims[2]))
            else:
                kept.append(term)
        if not (metadata or content or color or shape or resolution):
            return Description(raw)
    base = " ".join(kept)
    return Description(base, _text(content), color, shape, resolution)


def _color(hue: float, saturation: float, value: float) -> str:
    if value < .19:
        return "black"
    if saturation < .18:
        return "white" if value > .84 else "gray"
    angle = hue * 360
    if 12 <= angle < 48 and value < .59:
        return "brown"
    for end, name in ((13, "red"), (43, "orange"), (68, "yellow"), (155, "green"),
                      (200, "cyan"), (259, "blue"), (288, "purple"), (346, "pink")):
        if angle < end:
            return name
    return "red"


@lru_cache(maxsize=256)
def _image(path: str, size: int, mtime_ns: int, colors: bool) -> tuple[int, int, frozenset[str]]:
    try:
        from PIL import Image
    except ImportError:
        return 0, 0, frozenset()
    try:
        with Image.open(path) as img:
            width, height = img.size
            if width * height <= MAX_IMAGE_PIXELS and img.getexif().get(274, 1) in (5, 6, 7, 8):
                width, height = height, width
            if not colors:
                return width, height, frozenset()
            img.draft("RGB", (64, 64))
            if img.width * img.height > MAX_IMAGE_PIXELS:
                return width, height, frozenset()
            sample = img.convert("RGBA").resize((32, 32), Image.Resampling.BOX)
            pixels = sample.tobytes()
            counts = Counter(_color(*colorsys.rgb_to_hsv(r / 255, g / 255, b / 255))
                             for r, g, b, alpha in zip(pixels[::4], pixels[1::4], pixels[2::4], pixels[3::4])
                             if alpha >= 128)
        total = sum(counts.values())
        return width, height, frozenset(color for color, count in counts.items() if count >= total * .4)
    except (OSError, ValueError, Image.DecompressionBombError):
        return 0, 0, frozenset()


def _content(path: str, ext: str, needle: str) -> bool:
    if ext == "pdf":
        try:
            from pypdf import PdfReader
            import time
            deadline = time.monotonic() + PDF_EXTRACT_DEADLINE_S
            with open(path, "rb") as file:
                reader = PdfReader(file)
                total = 0
                for page in reader.pages[:24]:
                    if time.monotonic() > deadline:
                        break
                    text = page.extract_text() or ""
                    if needle in _text(text):
                        return True
                    total += len(text)
                    if total > MAX_PDF_CHARS:
                        break
                return False
        except Exception:
            return False
    if ext in OFFICE:
        try:
            with zipfile.ZipFile(path) as archive:
                remaining = MAX_TEXT_BYTES
                for part in archive.infolist():
                    relevant = part.filename.endswith((".html", ".xhtml")) if ext == "epub" else OFFICE_PART.fullmatch(part.filename)
                    if relevant and 0 < part.file_size <= remaining:
                        remaining -= part.file_size
                        body = archive.read(part).decode("utf-8", "replace")
                        if ext in ("docx", "xlsx", "pptx"):
                            text = " ".join("".join(OFFICE_RUN.findall(block)) for block in OFFICE_BLOCK.split(body))
                        else:
                            text = re.sub(r"<[^>]+>", " ", body)
                        if needle in _text(html.unescape(text)):
                            return True
            return False
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile):
            return False
    with open(path, "rb") as file:
        data = file.read(MAX_TEXT_BYTES)
    for encoding in (("utf-16",) if data.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "cp1251")):
        try:
            return needle in _text(data.decode(encoding))
        except UnicodeDecodeError:
            continue
    return False
