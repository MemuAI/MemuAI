# -*- coding: utf-8 -*-
"""MemuUtils core: the utility spec, inputs, outputs and the bilingual error.

A utility is a plain function ``fn(ctx) -> result`` plus a :class:`Util` spec the window renders a form from and the
chat turns into one prompt line. ``ctx`` gives the inputs (``text``, ``text2``, ``files``), the cleaned options
(``ctx.o``) and the output helpers; the result is a ``str`` or a dict with ``text`` / ``table`` / ``info`` / ``note``.
Files a utility writes go through :meth:`Ctx.out_bytes` / :meth:`Ctx.out_path`, so the runner knows every output.
"""
from __future__ import annotations

import base64
import mimetypes
import re
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

MAX_FILE_BYTES = 300 * 1024 * 1024
MAX_FILES = 40
MAX_TEXT = 3_000_000

class UtilError(ValueError):
    """A user-facing failure with a Russian and an English text."""

    def __init__(self, ru: str, en: str = "") -> None:
        super().__init__(en or ru)
        self.ru, self.en = ru, en or ru

    def text(self, lang: str) -> str:
        return self.en if lang == "en" else self.ru

@dataclass
class Opt:
    key: str
    kind: str            # text | textarea | number | int | bool | choice | color | password
    label: str
    label_en: str
    default: Any = None
    choices: Sequence[Any] = ()   # [(value, ru, en)] or plain values
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    hint: str = ""
    hint_en: str = ""
    show: str = ""       # "other=value|value": shown only when another option has one of these values
    ai: bool = True      # listed in the AI line

    def public(self, en: bool) -> Dict[str, Any]:
        row: Dict[str, Any] = {"key": self.key, "kind": self.kind, "label": self.label_en if en else self.label,
                               "default": self.default}
        if self.choices:
            row["choices"] = [{"value": c[0], "label": (c[2] if en else c[1]) if len(c) > 2 else str(c[1] if len(c) > 1 else c[0])}
                              if isinstance(c, (tuple, list)) else {"value": c, "label": str(c)} for c in self.choices]
        for name in ("min", "max", "step"):
            if getattr(self, name) is not None:
                row[name] = getattr(self, name)
        hint = self.hint_en if en else self.hint
        if hint:
            row["hint"] = hint
        if self.show:
            row["show"] = self.show
        return row

    def values(self) -> List[Any]:
        return [c[0] if isinstance(c, (tuple, list)) else c for c in self.choices]

def O(key: str, kind: str, label: str, label_en: str, default: Any = None, **kw: Any) -> Opt:
    return Opt(key, kind, label, label_en, default, **kw)

@dataclass
class Util:
    id: str
    cat: str
    title: str
    title_en: str
    desc: str
    desc_en: str
    fn: Callable[["Ctx"], Any]
    input: str = "text"          # text | text2 | file | files | none
    accept: str = ""             # file picker filter: ".pdf", "image/*", ".csv,.tsv"
    opts: List[Opt] = field(default_factory=list)
    ai: bool = True              # may be offered to the AI at all
    ai_default: bool = False     # offered to the AI out of the box
    danger: bool = False         # changes the system or probes other hosts: the chat asks the user first
    danger_if: str = ""          # dangerous only with these options: "delete" (truthy) or "mode=delete|apply"
    platform: str = ""           # "win" = Windows only
    keys: str = ""               # extra search words (both languages)
    live: bool = False           # cheap and pure: the window re-runs it while typing
    placeholder: str = ""
    placeholder_en: str = ""
    label2: str = ""             # the second text box (input == "text2")
    label2_en: str = ""
    example: str = ""
    prompt: str = ""             # AI utilities: the instructions the window sends with Memu.request ({opt} filled in)
    prompt_en: str = ""
    pick: str = ""               # "folder": the text box holds a folder path (the window offers a folder picker)

    def is_danger(self, opts: Dict[str, Any]) -> bool:
        if self.danger:
            return True
        if not self.danger_if:
            return False
        key, _, values = self.danger_if.partition("=")
        value = (opts or {}).get(key)
        if not values:
            return bool(value) and str(value).lower() not in ("0", "false", "no")
        return str(value) in values.split("|")

    def public(self, en: bool) -> Dict[str, Any]:
        row = {"id": self.id, "cat": self.cat, "title": self.title_en if en else self.title,
               "desc": self.desc_en if en else self.desc, "input": self.input, "accept": self.accept,
               "opts": [o.public(en) for o in self.opts], "ai": self.ai, "ai_default": self.ai_default,
               "danger": self.danger, "danger_if": self.danger_if, "platform": self.platform, "live": self.live,
               "keys": " ".join(filter(None, [self.keys, self.title, self.title_en])).lower()}
        placeholder = self.placeholder_en if en else self.placeholder
        if placeholder:
            row["placeholder"] = placeholder
        if self.input == "text2":
            row["label2"] = (self.label2_en if en else self.label2) or ("Second text" if en else "Второй текст")
        if self.example:
            row["example"] = self.example
        if self.prompt:
            row["prompt"] = self.prompt_en if en and self.prompt_en else self.prompt
        if self.pick:
            row["pick"] = self.pick
        from .ui_hints import ui_for
        ui = ui_for(self, en)
        if ui:
            row["ui"] = ui
        return row

def safe_name(name: str, fallback: str = "file") -> str:
    base = Path(str(name or "")).name
    base = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", base).strip(" .")
    if base.split(".", 1)[0].strip().upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                                 *(f"LPT{i}" for i in range(1, 10))}:
        base = "_" + base
    return base[:140] or fallback

def decode_data(value: Any) -> bytes:
    """A data URL, plain base64 or bytes → bytes."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    text = str(value or "")
    if text.startswith("data:"):
        meta, _, payload = text.partition(",")
        if ";base64" in meta:
            return base64.b64decode(payload + "=" * (-len(payload) % 4))
        return urllib.parse.unquote_to_bytes(payload)
    return base64.b64decode(text + "=" * (-len(text) % 4))

def guess_mime(name: str) -> str:
    ext = Path(name).suffix.lower()
    extra = {".webp": "image/webp", ".avif": "image/avif", ".heic": "image/heic", ".opus": "audio/ogg",
             ".m4a": "audio/mp4", ".flac": "audio/flac", ".mkv": "video/x-matroska", ".md": "text/markdown",
             ".yaml": "text/yaml", ".yml": "text/yaml", ".jsonl": "application/x-ndjson", ".ico": "image/x-icon",
             ".parquet": "application/vnd.apache.parquet", ".epub": "application/epub+zip", ".fb2": "application/x-fictionbook+xml",
             ".svg": "image/svg+xml", ".ts": "text/plain", ".tsv": "text/tab-separated-values"}
    return extra.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"

def kind_of(name: str) -> str:
    mime = guess_mime(name)
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("text/") or mime in ("application/json", "application/xml", "application/x-ndjson"):
        return "text"
    return "file"

def human_size(size: float, en: bool = False) -> str:
    units = ("B", "KB", "MB", "GB", "TB") if en else ("Б", "КБ", "МБ", "ГБ", "ТБ")
    size = float(size)
    for unit in units:
        if abs(size) < 1024 or unit == units[-1]:
            return (f"{size:.0f} {unit}" if unit == units[0] else f"{size:.1f} {unit}").replace(".", "." if en else ",")
        size /= 1024
    return f"{size:.1f}"

class InFile:
    """One input file: bytes in memory, written to the work folder on first :meth:`path`."""

    def __init__(self, name: str, data: bytes, work: Path) -> None:
        self.name = safe_name(name)
        self.data = data
        self.work = work
        self._path: Optional[Path] = None

    @property
    def ext(self) -> str:
        return Path(self.name).suffix.lower().lstrip(".")

    @property
    def stem(self) -> str:
        return Path(self.name).stem or "file"

    @property
    def size(self) -> int:
        return len(self.data)

    def path(self) -> Path:
        if self._path is None:
            target = self.work / "in" / f"{uuid.uuid4().hex[:6]}-{self.name}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.data)
            self._path = target
        return self._path

    def text(self) -> str:
        """The file as text: BOM, UTF-16 and single-byte code pages (cp1251, koi8-r…) are detected."""
        try:
            from ..memunote.formats import textio
            info = textio.detect_bytes(self.data[:262144], len(self.data) <= 262144)
            return textio.decode(self.data, info.encoding)
        except Exception:  # noqa: BLE001 - any detector failure falls back to a forgiving UTF-8 read
            return self.data.decode("utf-8", "replace")

class Ctx:
    """What a utility function sees."""

    def __init__(self, util: Util, text: str, text2: str, files: List[InFile], opts: Dict[str, Any], lang: str,
                 work: Path, source: str) -> None:
        self.util, self.text, self.text2, self.files, self.o = util, text, text2, files, opts
        self.lang, self.en, self.work, self.source = lang, lang == "en", work, source
        self.out_dir = work / "out"
        self.outputs: List[Path] = []
        self.pdf_jobs: List[tuple] = []

    def tr(self, ru: str, en: str) -> str:
        return en if self.en else ru

    def file(self, index: int = 0) -> InFile:
        if len(self.files) <= index:
            raise UtilError("Добавьте файл", "Add a file")
        return self.files[index]

    def need_text(self, what: str = "", what_en: str = "") -> str:
        if not str(self.text or "").strip():
            raise UtilError(what or "Введите текст", what_en or "Enter some text")
        return self.text

    def _unique(self, name: str) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        target = self.out_dir / safe_name(name, "result")
        stem, suffix, index = target.stem, target.suffix, 2
        while target.exists() or target in self.outputs:
            target = self.out_dir / f"{stem} ({index}){suffix}"
            index += 1
        return target

    def out_path(self, name: str) -> Path:
        """A path to write an output to directly (big media): registered as an output right away."""
        target = self._unique(name)
        self.outputs.append(target)
        return target

    def out_bytes(self, name: str, data: bytes) -> Path:
        target = self.out_path(name)
        target.write_bytes(data)
        return target

    def out_text(self, name: str, text: str) -> Path:
        return self.out_bytes(name, text.encode("utf-8"))

    def pdf_job(self, html: str, name: str, text: str = "") -> Path:
        """A PDF printed from *html* once the utility returns (headless Edge; a raster page fallback from *text*)."""
        target = self.out_path(name)
        self.pdf_jobs.append((html, target, text))
        return target

def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default

def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default

def parse_time(value: Any) -> float:
    """``90``, ``1:30``, ``01:02:03.5`` → seconds."""
    text = str(value or "").strip().replace(",", ".")
    if not text:
        return 0.0
    parts = text.split(":")
    try:
        total = 0.0
        for part in parts:
            total = total * 60 + float(part or 0)
        return max(0.0, total)
    except ValueError:
        raise UtilError(f"Не понимаю время «{text}»: нужно 90, 1:30 или 01:02:03", f"Cannot read the time “{text}”: use 90, 1:30 or 01:02:03")

def parse_color(value: Any, default=(0, 0, 0)):
    from .colors import parse as _parse
    try:
        return _parse(str(value or ""))
    except ValueError:
        if default is None:
            raise
        return default

def page_ranges(spec: str, total: int) -> List[int]:
    """``"1-3,5,8-"`` → 0-based page indexes (all pages when empty)."""
    spec = str(spec or "").strip().replace(" ", "")
    if not spec or spec.lower() in ("all", "все", "*"):
        return list(range(total))
    out: List[int] = []
    for part in spec.split(","):
        if not part:
            continue
        m = re.fullmatch(r"(\d*)-(\d*)", part)
        if m:
            a = int(m.group(1) or 1)
            b = int(m.group(2) or total)
        elif part.isdigit():
            a = b = int(part)
        else:
            raise UtilError(f"Непонятный диапазон «{part}»: пример 1-3,5,8-", f"Bad range “{part}”: e.g. 1-3,5,8-")
        if a > b:
            a, b = b, a
        for n in range(max(1, a), min(total, b) + 1):
            if n - 1 not in out:
                out.append(n - 1)
    if not out:
        raise UtilError(f"В документе {total} стр. — диапазон пуст", f"The document has {total} pages — the range is empty")
    return out

REGISTRY: Dict[str, Util] = {}

def utility(uid: str, cat: str, title: str, title_en: str, desc: str, desc_en: str, **kw: Any):
    """Register ``fn`` as the utility ``uid`` (ids are ``category.name``, unique)."""
    def wrap(fn: Callable[[Ctx], Any]) -> Callable[[Ctx], Any]:
        if uid in REGISTRY:
            raise ValueError(f"duplicate utility {uid}")
        REGISTRY[uid] = Util(uid, cat, title, title_en, desc, desc_en, fn, **kw)
        return fn
    return wrap

def lines_of(text: str) -> List[str]:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")

def table(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> Dict[str, Any]:
    return {"columns": [str(c) for c in columns], "rows": [["" if v is None else v for v in row] for row in rows]}
