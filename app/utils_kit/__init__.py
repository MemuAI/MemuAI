# -*- coding: utf-8 -*-
"""MemuUtils: a large set of local utilities (converters, encoders, formatters, validators, generators, image, audio,
video, PDF, documents, data, network, system and a few AI ones) for the MemuUtils window and for the AI in the chat.

``catalog()`` is the spec the window renders; ``run_async()`` executes one utility in a work folder
``<data>/utils/runs/<run>/`` and describes its outputs; ``skill_text()`` is the compact tool list for the AI, built only
from the utilities the person allowed in the window (token budget)."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .core import (MAX_FILE_BYTES, MAX_FILES, MAX_TEXT, REGISTRY, Ctx, InFile, Util, UtilError, as_float, as_int,
                   decode_data, guess_mime, human_size, kind_of, safe_name)
# registration order = the window order inside a category
from . import t_text, t_dev, t_code, t_check, t_gen, t_image, t_av, t_docs, t_data, t_net, t_sys, t_ai  # noqa: F401,E402
from . import jobs  # noqa: E402

log = logging.getLogger("memuai.utils")

CATEGORIES: List[Tuple[str, str, str]] = [
    ("text", "Текст", "Text"), ("format", "Форматирование", "Formatting"), ("code", "Кодирование", "Encoding"),
    ("dev", "Разработчику", "Developer"), ("check", "Проверка", "Validation"), ("convert", "Пересчёт величин", "Conversions"),
    ("gen", "Генераторы", "Generators"), ("image", "Изображения", "Images"), ("audio", "Аудио", "Audio"), ("video", "Видео", "Video"),
    ("pdf", "PDF", "PDF"), ("docs", "Документы", "Documents"), ("data", "Таблицы и данные", "Tables and data"),
    ("net", "Сеть", "Network"), ("system", "Система", "System"), ("ai", "С ИИ", "With AI"),
]
CAT_IDS = [c[0] for c in CATEGORIES]
RUN_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
UP_RE = re.compile(r"^[0-9a-f]{16}$")
RUN_TTL = 24 * 3600
RUN_KEEP = 80
UPLOAD_TTL = 6 * 3600
PREVIEW_LIMIT = 40 * 1024 * 1024
DATA_URL_LIMIT = 60 * 1024 * 1024
_LOCK = threading.RLock()

# ── storage ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def data_root() -> Path:
    env = os.environ.get("MEMUAI_DATA_DIR") or os.environ.get("OMNIHUB_DATA_DIR")
    if env:
        return Path(env) / "utils"
    from ..config import DATA_DIR
    return Path(DATA_DIR) / "utils"

def runs_dir() -> Path:
    return data_root() / "runs"

def uploads_dir() -> Path:
    return data_root() / "uploads"

def platform_ok(util: Util) -> bool:
    return util.platform != "win" or sys.platform == "win32"

DEFAULT_SETTINGS: Dict[str, Any] = {"ai_enabled": True, "ai_tools": None, "favorites": [], "recent": [], "model": "", "view": "grid"}

def load_settings() -> Dict[str, Any]:
    path = data_root() / "settings.json"
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        raw = {}
    out = dict(DEFAULT_SETTINGS)
    if isinstance(raw, dict):
        out.update({k: v for k, v in raw.items() if k in DEFAULT_SETTINGS})
    out["favorites"] = [i for i in out.get("favorites") or [] if i in REGISTRY][:200]
    out["recent"] = [i for i in out.get("recent") or [] if i in REGISTRY][:12]
    if out.get("ai_tools") is not None:
        out["ai_tools"] = [i for i in out["ai_tools"] if i in REGISTRY and REGISTRY[i].ai]
    return out

def save_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        current = load_settings()
        for key, value in (patch or {}).items():
            if key == "ai_enabled":
                current[key] = bool(value)
            elif key == "ai_tools":
                current[key] = None if value is None else [str(i) for i in value if str(i) in REGISTRY and REGISTRY[str(i)].ai][:400]
            elif key in ("favorites", "recent"):
                current[key] = [str(i) for i in value or [] if str(i) in REGISTRY][:200]
            elif key == "model":
                current[key] = str(value or "")[:200]
            elif key == "view":
                current[key] = "list" if value == "list" else "grid"
        path = data_root() / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, path)
        return current

def recommended_tools() -> List[str]:
    """The suggested starter set (AI access → «Рекомендуемые»): everyday converters, checks and generators."""
    return [u.id for u in REGISTRY.values() if u.ai and u.ai_default and platform_ok(u)]

def ai_tools(settings: Optional[Dict[str, Any]] = None) -> List[str]:
    """The utilities the AI may call: only the ones the person allowed in MemuUtils → AI access.

    Nothing is allowed out of the box (``ai_tools`` is ``None`` until the first choice); the «Рекомендуемые» preset
    writes :func:`recommended_tools` as an explicit list."""
    s = settings or load_settings()
    chosen = s.get("ai_tools")
    if not chosen:
        return []
    return [i for i in chosen if i in REGISTRY and REGISTRY[i].ai and platform_ok(REGISTRY[i])]

def _remember(util_id: str) -> None:
    try:
        s = load_settings()
        recent = [util_id] + [i for i in s["recent"] if i != util_id]
        save_settings({"recent": recent[:12]})
    except OSError:
        pass

# ── catalog and the AI line ──────────────────────────────────────────────────────────────────────────────────────────
def _opt_hint(o) -> str:
    if o.kind == "bool":
        return f"{o.key}={'true' if o.default else 'false'}"
    if o.kind == "choice":
        values = [str(v) for v in o.values()]
        default = str(o.default)
        rest = [v for v in values if v != default]
        shown = [default] + rest[:6]
        return f"{o.key}=" + "|".join(shown).replace("\t", "\\t") + ("|…" if len(rest) > 6 else "")
    if o.kind in ("int", "number"):
        return f"{o.key}={o.default}"
    if o.kind == "password":
        return ""
    default = str(o.default or "")
    return f'{o.key}="{default[:24]}"' if default else f"{o.key}=…"

INPUT_HINT = {"text": "text", "text2": "text+text2", "file": "file", "files": "files", "any": "text|file", "none": "-"}

def tool_line(util: Util, en: bool = True) -> str:
    hints, size = [], 0
    for o in util.opts:
        hint = _opt_hint(o) if o.ai else ""
        if not hint:
            continue
        if size + len(hint) > 150:
            hints.append("…")
            break
        hints.append(hint)
        size += len(hint) + 1
    danger = "!" if util.danger or util.danger_if else ""
    title = util.title_en if en else util.title
    return f"- {util.id}{danger} · {INPUT_HINT.get(util.input, util.input)}" + (f" · {' '.join(hints)}" if hints else "") + f" — {title}"

def estimate_tokens(text: str) -> int:
    # ~3.6 characters per token for this mixed Latin text; Cyrillic is denser in tokens
    cyr = len(re.findall(r"[А-Яа-яЁё]", text))
    return int((len(text) - cyr) / 3.6 + cyr / 2.2) + 1

SKILL_HEAD = ("MemuUtils runs local utilities on the user's PC: converters (images, audio, video, documents, tables), PDF tools, "
              "encoders/decoders, formatters, validators, generators, network checks and more. When the user asks for such a job, run it "
              "with module_command {\"module\":\"utils\",\"command\":\"run\",\"args\":{\"util\":\"<tool id>\",\"text\":\"…\",\"opts\":{…}}} "
              "instead of explaining how to do it by hand.\n"
              "Files: the files the user attached in this chat are passed automatically; \"file\": \"\" = the latest attached file(s), "
              "a file name = that attachment, \"all\" = every recent attachment, \"out:<run>/<name>\" = an output of an earlier run "
              "(to chain tools; several: comma-separated). Videos attached to the chat arrive as frames only, so for video tools ask the "
              "user to open MemuUtils (command open). text2: the second text of tools that compare or combine two. Options not given "
              "take their defaults (the first value shown). The result card shows the outputs with Save and Show buttons: describe the "
              "result briefly, do not paste files back. Tools marked ! ask the user to confirm. Command info {\"util\"} gives every "
              "option of one tool.")

def skill_text(lang: str = "en") -> str:
    s = load_settings()
    if not s.get("ai_enabled", True):
        return ("MemuUtils: the user turned off AI access to the utilities (MemuUtils window → AI access). "
                "If they ask for a conversion or a check, suggest opening MemuUtils or turning the access on.")
    ids = ai_tools(s)
    if not ids:
        return "MemuUtils: no utilities are allowed for the AI (MemuUtils window → AI access)."
    lines = [SKILL_HEAD, "Tools (id · input · options — what):"]
    by_cat: Dict[str, List[str]] = {}
    for uid in ids:
        u = REGISTRY[uid]
        by_cat.setdefault(u.cat, []).append(tool_line(u, True))
    for cat in CAT_IDS:
        lines += by_cat.get(cat, [])
    return "\n".join(lines)

def skill_stats() -> Dict[str, Any]:
    text = skill_text("en")
    return {"tools": len(ai_tools()), "tokens": estimate_tokens(text), "chars": len(text)}

def tool_help(util_id: str, lang: str = "ru") -> str:
    util = REGISTRY.get(str(util_id or ""))
    if util is None:
        raise UtilError(f"Нет утилиты «{util_id}»", f"No utility “{util_id}”")
    en = lang == "en"
    rows = [f"{util.id} — {util.title_en if en else util.title}", util.desc_en if en else util.desc, f"input: {INPUT_HINT.get(util.input, util.input)}"
            + (f" ({util.accept})" if util.accept else "")]
    for o in util.opts:
        if not o.ai:
            continue
        spec = o.public(en)
        line = f"  {o.key} ({o.kind}, default {o.default!r}): {spec['label']}"
        if spec.get("choices"):
            line += " — " + ", ".join(f"{c['value']}={c['label']}" for c in spec["choices"][:14])
        if spec.get("hint"):
            line += f" [{spec['hint']}]"
        rows.append(line)
    if util.danger or util.danger_if:
        rows.append("asks the user to confirm" + (f" when {util.danger_if}" if util.danger_if and not util.danger else ""))
    return "\n".join(rows)

def catalog(lang: str = "ru") -> Dict[str, Any]:
    en = lang == "en"
    s = load_settings()
    allowed = set(ai_tools(s))
    utils = []
    counts: Dict[str, int] = {}
    for u in REGISTRY.values():
        row = u.public(en)
        row["ai_on"] = u.id in allowed
        row["available"] = platform_ok(u)
        row["tokens"] = estimate_tokens(tool_line(u, True)) if u.ai else 0
        utils.append(row)
        counts[u.cat] = counts.get(u.cat, 0) + 1
    cats = [{"id": cid, "title": en_t if en else ru_t, "count": counts.get(cid, 0)} for cid, ru_t, en_t in CATEGORIES if counts.get(cid)]
    from .ui_hints import TOP
    top = [uid for uid in TOP if uid in REGISTRY and platform_ok(REGISTRY[uid])]
    return {"categories": cats, "utils": utils, "settings": s, "platform": sys.platform, "skill": skill_stats(), "jobs": jobs.listing(en),
            "total": len(utils), "top": top, "recommended": recommended_tools()}

# ── options and inputs ───────────────────────────────────────────────────────────────────────────────────────────────
def clean_opts(util: Util, raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    out: Dict[str, Any] = {}
    for o in util.opts:
        value = raw.get(o.key, o.default)
        if o.kind == "bool":
            value = value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on", "да")
        elif o.kind == "int":
            value = as_int(value, int(o.default or 0))
            if o.min is not None:
                value = max(int(o.min), value)
            if o.max is not None:
                value = min(int(o.max), value)
        elif o.kind == "number":
            value = as_float(value, float(o.default or 0))
            if o.min is not None:
                value = max(float(o.min), value)
            if o.max is not None:
                value = min(float(o.max), value)
        elif o.kind == "choice":
            values = o.values()
            match = next((v for v in values if str(v) == str(value)), None)
            if match is None:
                match = next((v for v in values if str(v).lower() == str(value).strip().lower()), o.default)
            value = match
        else:
            value = "" if value is None else str(value)[:200_000]
        out[o.key] = value
    return out

class _PathFile(InFile):
    """An input that already lives on disk (an upload or an earlier output): read lazily, never copied."""

    def __init__(self, name: str, path: Path, work: Path) -> None:
        super().__init__(name, b"", work)
        self._disk = path
        self._loaded: Optional[bytes] = None
        self._path = path

    @property
    def data(self) -> bytes:  # type: ignore[override]
        if self._loaded is None:
            self._loaded = self._disk.read_bytes()
        return self._loaded

    @data.setter
    def data(self, value: bytes) -> None:
        self._loaded = value or None

    @property
    def size(self) -> int:  # type: ignore[override]
        try:
            return self._disk.stat().st_size
        except OSError:
            return 0

def _inside(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False

def resolve_ref(ref: str) -> Path:
    """``out:<run>/<name>`` or ``up:<id>`` → a file path inside the MemuUtils folders."""
    ref = str(ref or "").strip()
    if ref.startswith("out:"):
        run, _, name = ref[4:].partition("/")
        if not RUN_RE.match(run) or not name:
            raise UtilError("Неверная ссылка на результат", "Bad output reference")
        path = runs_dir() / run / "out" / safe_name(name)
        base = runs_dir()
    elif ref.startswith("up:"):
        up = ref[3:]
        if not UP_RE.match(up):
            raise UtilError("Неверная ссылка на загрузку", "Bad upload reference")
        folder = uploads_dir() / up
        files = [p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")] if folder.is_dir() else []
        if not files:
            raise UtilError("Загруженный файл не найден (устарел?) — добавьте его снова", "The uploaded file is gone (expired?) — add it again")
        path = files[0]
        base = uploads_dir()
    else:
        raise UtilError("Неверная ссылка на файл", "Bad file reference")
    if not _inside(base, path) or not path.is_file():
        raise UtilError("Файл результата не найден (результаты хранятся сутки)", "The output file is gone (outputs are kept for a day)")
    return path

def resolve_files(entries: Any, work: Path) -> List[InFile]:
    files: List[InFile] = []
    if not isinstance(entries, list):
        entries = [entries] if entries else []
    total = 0
    for entry in entries[:MAX_FILES]:
        if isinstance(entry, str):
            entry = {"ref": entry}
        if not isinstance(entry, dict):
            continue
        ref = str(entry.get("ref") or "")
        if ref:
            path = resolve_ref(ref)
            files.append(_PathFile(entry.get("name") or path.name, path, work))
            continue
        if entry.get("data") is not None:
            try:
                data = decode_data(entry["data"])
            except (ValueError, TypeError):
                raise UtilError(f"Файл «{entry.get('name') or 'file'}» повреждён при передаче", f"The file “{entry.get('name') or 'file'}” was damaged in transfer")
            total += len(data)
            if len(data) > MAX_FILE_BYTES or total > MAX_FILE_BYTES * 2:
                raise UtilError("Слишком большой файл", "The file is too large")
            files.append(InFile(str(entry.get("name") or "file"), data, work))
    return files

# ── uploads (big files from the window, in chunks) ───────────────────────────────────────────────────────────────────
def upload_start(name: str, size: int) -> Dict[str, Any]:
    if int(size or 0) > MAX_FILE_BYTES:
        raise UtilError(f"Файл больше {human_size(MAX_FILE_BYTES)}", f"The file is larger than {human_size(MAX_FILE_BYTES, True)}")
    prune()
    up = uuid.uuid4().hex[:16]
    folder = uploads_dir() / up
    folder.mkdir(parents=True, exist_ok=True)
    (folder / safe_name(name)).write_bytes(b"")
    (folder / ".meta").write_text(json.dumps({"name": safe_name(name), "size": int(size or 0), "t": time.time()}), "utf-8")
    return {"upload": up}

def upload_chunk(up: str, offset: int, data: str) -> Dict[str, Any]:
    if not UP_RE.match(str(up or "")):
        raise UtilError("Неверная загрузка", "Bad upload")
    folder = uploads_dir() / up
    try:
        meta = json.loads((folder / ".meta").read_text("utf-8"))
    except (OSError, ValueError):
        raise UtilError("Загрузка не найдена", "Upload not found")
    path = folder / meta["name"]
    raw = decode_data(data)
    if path.stat().st_size != int(offset):
        raise UtilError("Части файла пришли не по порядку — попробуйте ещё раз", "File parts arrived out of order — try again")
    if path.stat().st_size + len(raw) > max(int(meta.get("size") or 0), 1) + 16:
        raise UtilError("Файл больше заявленного", "The file is larger than declared")
    with open(path, "ab") as fh:
        fh.write(raw)
    return {"received": path.stat().st_size}

def upload_done(up: str) -> Dict[str, Any]:
    folder = uploads_dir() / str(up or "")
    if not UP_RE.match(str(up or "")) or not folder.is_dir():
        raise UtilError("Загрузка не найдена", "Upload not found")
    meta = json.loads((folder / ".meta").read_text("utf-8"))
    path = folder / meta["name"]
    if path.stat().st_size != int(meta.get("size") or 0):
        raise UtilError("Файл загрузился не полностью", "The file did not upload completely")
    return {"ref": f"up:{up}", "name": meta["name"], "size": path.stat().st_size}

def prune() -> None:
    now = time.time()
    for base, ttl, keep in ((runs_dir(), RUN_TTL, RUN_KEEP), (uploads_dir(), UPLOAD_TTL, 200)):
        if not base.is_dir():
            continue
        try:
            items = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            continue
        for index, item in enumerate(items):
            try:
                if index >= keep or now - item.stat().st_mtime > ttl:
                    shutil.rmtree(item, ignore_errors=True)
            except OSError:
                continue

# ── running ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]

def _thumb(path: Path) -> Dict[str, Any]:
    try:
        from PIL import Image
        try:
            import pillow_heif  # type: ignore
            pillow_heif.register_heif_opener()
        except Exception:  # noqa: BLE001
            pass
        with Image.open(path) as img:
            size = img.size
            img.seek(0)
            frame = img.convert("RGBA")
        frame.thumbnail((420, 420))
        buf = io.BytesIO()
        frame.save(buf, "PNG", optimize=True)
        if buf.tell() > 180_000:
            buf = io.BytesIO()
            frame.convert("RGB").save(buf, "JPEG", quality=82)
            mime = "image/jpeg"
        else:
            mime = "image/png"
        return {"preview": f"data:{mime};base64," + base64.b64encode(buf.getvalue()).decode(), "width": size[0], "height": size[1]}
    except Exception:  # noqa: BLE001 - no preview for an exotic image
        return {}

def describe(path: Path, run_id: str, en: bool) -> Dict[str, Any]:
    st = path.stat()
    kind = kind_of(path.name)
    row: Dict[str, Any] = {"name": path.name, "size": st.st_size, "size_h": human_size(st.st_size, en), "mime": guess_mime(path.name),
                           "kind": kind, "ref": f"out:{run_id}/{path.name}"}
    if kind == "image" and st.st_size <= PREVIEW_LIMIT and path.suffix.lower() != ".svg":
        row.update(_thumb(path))
    return row

def normalize(result: Any) -> Dict[str, Any]:
    if result is None:
        return {}
    if isinstance(result, str):
        return {"text": result}
    if not isinstance(result, dict):
        return {"text": str(result)}
    out: Dict[str, Any] = {}
    for key in ("text", "lang", "note", "mono"):
        if result.get(key) not in (None, ""):
            out[key] = result[key] if key == "mono" else str(result[key])
    if isinstance(result.get("table"), dict):
        out["table"] = result["table"]
    if isinstance(result.get("info"), list):
        out["info"] = [[str(a), str(b)] for a, b in result["info"]]
    if isinstance(result.get("swatches"), list):
        out["swatches"] = [str(x) for x in result["swatches"]][:64]
    if isinstance(result.get("job"), dict):
        out["job"] = result["job"]
    if "text" in out and len(out["text"]) > 400_000:
        out["text"] = out["text"][:400_000]
        out["note"] = (out.get("note", "") + " · …").strip(" ·")
    return out

def _execute(util: Util, text: str, text2: str, files: Any, opts: Dict[str, Any], lang: str, source: str, run_id: str) -> Tuple[Ctx, Dict[str, Any]]:
    work = runs_dir() / run_id
    work.mkdir(parents=True, exist_ok=True)
    inputs = resolve_files(files, work)
    ctx = Ctx(util, str(text or "")[:MAX_TEXT], str(text2 or "")[:MAX_TEXT], inputs, opts, lang, work, source)
    return ctx, normalize(util.fn(ctx))

def raster_pdf(text: str) -> bytes:
    """A plain A4 PDF of *text* rendered by Pillow (no Edge available): selectable text is lost, the look is kept simple."""
    from PIL import Image, ImageDraw
    from .t_gen import font
    dpi, width, height, margin = 110, 910, 1286, 80
    fnt = font(16)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    lines: List[str] = []
    for para in str(text or "").replace("\r\n", "\n").split("\n"):
        words, line = para.split(" "), ""
        if not para.strip():
            lines.append("")
            continue
        for word in words:
            cand = (line + " " + word).strip() if line else word
            if probe.textlength(cand, font=fnt) <= width - 2 * margin:
                line = cand
            else:
                if line:
                    lines.append(line)
                while probe.textlength(word, font=fnt) > width - 2 * margin and len(word) > 1:
                    cut = len(word)
                    while cut > 1 and probe.textlength(word[:cut], font=fnt) > width - 2 * margin:
                        cut -= 1
                    lines.append(word[:cut])
                    word = word[cut:]
                line = word
        lines.append(line)
    per_page = (height - 2 * margin) // 24
    pages = []
    for start in range(0, max(1, len(lines)), per_page):
        img = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(img)
        for i, line in enumerate(lines[start:start + per_page]):
            draw.text((margin, margin + i * 24), line, font=fnt, fill=(24, 24, 24))
        pages.append(img)
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", resolution=dpi, save_all=True, append_images=pages[1:])
    return buf.getvalue()

async def _finish_pdf(ctx: Ctx) -> None:
    for html, target, text in ctx.pdf_jobs:
        data = b""
        try:
            from .. import chat_export_pdf
            if chat_export_pdf.available():
                data = await chat_export_pdf.html_to_pdf(html, {"page": "a4", "margin_mm": 18})
        except Exception as exc:  # noqa: BLE001 - Edge failed: the raster fallback below
            log.warning("utils pdf via edge failed: %s", str(exc).encode("ascii", "replace").decode())
            data = b""
        if not data:
            if not text:
                text = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", "", html))
                text = re.sub(r"[ \t]+", " ", text)
            data = await asyncio.to_thread(raster_pdf, text)
        target.write_bytes(data)

async def run_async(util_id: str, *, text: str = "", text2: str = "", files: Any = None, opts: Any = None, lang: str = "ru",
                    source: str = "window", confirmed: bool = True, remember: bool = True) -> Dict[str, Any]:
    """Run one utility. Never raises for user errors: ``{ok: False, error}``; ``{needs_confirm}`` for a dangerous
    call from the chat that the user has not confirmed yet."""
    en = lang == "en"
    util = REGISTRY.get(str(util_id or "").strip())
    if util is None:
        close = [u for u in REGISTRY if str(util_id or "").split(".")[-1] in u]
        return {"ok": False, "error": (f"No utility “{util_id}”" if en else f"Нет утилиты «{util_id}»") + (f". Similar: {', '.join(close[:6])}" if close else "")}
    if not platform_ok(util):
        return {"ok": False, "error": "This utility works on Windows only" if en else "Эта утилита работает только в Windows"}
    clean = clean_opts(util, opts)
    if source == "chat":
        if not util.ai:
            return {"ok": False, "error": "This utility runs only in the MemuUtils window" if en else "Эта утилита запускается только в окне MemuUtils"}
        s = load_settings()
        if not s.get("ai_enabled", True) or util.id not in ai_tools(s):
            return {"ok": False, "error": ("The user has not allowed the AI to use this utility (MemuUtils → AI access)" if en
                                           else "Пользователь не разрешил ИИ эту утилиту (MemuUtils → Доступ ИИ)")}
        if util.is_danger(clean) and not confirmed:
            return {"ok": False, "needs_confirm": True, "error": "The user has to confirm this" if en else "Нужно подтверждение пользователя"}
    prune()
    run_id = new_run_id()
    started = time.perf_counter()
    try:
        ctx, result = await asyncio.to_thread(_execute, util, text, text2, files, clean, lang, source, run_id)
        if ctx.pdf_jobs:
            await _finish_pdf(ctx)
    except UtilError as exc:
        shutil.rmtree(runs_dir() / run_id, ignore_errors=True)
        return {"ok": False, "util": util.id, "error": exc.text(lang)}
    except MemoryError:
        shutil.rmtree(runs_dir() / run_id, ignore_errors=True)
        return {"ok": False, "util": util.id, "error": "Not enough memory for this file" if en else "Не хватило памяти для этого файла"}
    except Exception as exc:  # noqa: BLE001 - a bug or an unexpected file must not take the window down
        log.exception("utility %s failed", util.id)
        shutil.rmtree(runs_dir() / run_id, ignore_errors=True)
        detail = f"{type(exc).__name__}: {exc}"[:400]
        return {"ok": False, "util": util.id, "error": (f"The utility failed: {detail}" if en else f"Утилита завершилась с ошибкой: {detail}")}
    outputs = [describe(p, run_id, en) for p in ctx.outputs if p.exists()]
    if not outputs and not any((runs_dir() / run_id / "out").glob("*")):
        shutil.rmtree(runs_dir() / run_id / "in", ignore_errors=True)
    if source == "window" and remember:
        _remember(util.id)
    payload = {"ok": True, "util": util.id, "title": util.title_en if en else util.title, "run": run_id, "result": result,
               "outputs": outputs, "ms": int((time.perf_counter() - started) * 1000)}
    _store_result(run_id, payload, source)
    return payload

def _store_result(run_id: str, payload: Dict[str, Any], source: str) -> None:
    """``result.json`` of a run: the window reopens a result the chat produced (card «Open MemuUtils»)."""
    try:
        keep = {k: payload[k] for k in ("util", "title", "run", "ms") if k in payload}
        keep["result"] = {k: v for k, v in (payload.get("result") or {}).items() if k != "job"}
        keep["source"], keep["t"] = source, time.time()
        (runs_dir() / run_id).mkdir(parents=True, exist_ok=True)
        (runs_dir() / run_id / "result.json").write_text(json.dumps(keep, ensure_ascii=False), "utf-8")
    except (OSError, TypeError, ValueError):
        pass

def run_info(run: str, lang: str = "ru") -> Dict[str, Any]:
    """A stored run for the window: ``{util, title, result, outputs, source}`` (outputs re-described, thumbnails included)."""
    if not RUN_RE.match(str(run or "")):
        raise UtilError("Неверный идентификатор запуска", "Bad run id")
    folder = runs_dir() / run
    if not folder.is_dir():
        raise UtilError("Результат не найден (результаты хранятся сутки)", "The result is gone (results are kept for a day)")
    try:
        data = json.loads((folder / "result.json").read_text("utf-8"))
    except (OSError, ValueError):
        data = {}
    util = REGISTRY.get(str(data.get("util") or ""))
    if not util:
        try:
            meta = json.loads((folder / "meta.json").read_text("utf-8"))
            util = REGISTRY.get(str(meta.get("util") or ""))
        except (OSError, ValueError):
            pass
    out_dir = folder / "out"
    files = sorted((p for p in out_dir.glob("*") if p.is_file()), key=lambda p: p.name) if out_dir.is_dir() else []
    en = lang == "en"
    return {"run": run, "util": util.id if util else str(data.get("util") or ""),
            "title": (util.title_en if en else util.title) if util else str(data.get("title") or ""),
            "result": data.get("result") or {}, "source": data.get("source") or "", "ms": data.get("ms") or 0,
            "outputs": [describe(p, run, en) for p in files]}

# ── outputs ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def output_path(run: str, name: str) -> Path:
    return resolve_ref(f"out:{run}/{name}")

def output_data(run: str, name: str) -> Dict[str, Any]:
    path = output_path(run, name)
    size = path.stat().st_size
    if size > DATA_URL_LIMIT:
        raise UtilError("Файл слишком большой для предпросмотра — сохраните его", "The file is too large to preview — save it")
    return {"name": path.name, "mime": guess_mime(path.name), "size": size,
            "data": f"data:{guess_mime(path.name)};base64," + base64.b64encode(path.read_bytes()).decode()}

def save_folder() -> Path:
    try:
        from ..quick_apps import downloads_folder
        base = Path(downloads_folder())
    except Exception:  # noqa: BLE001
        base = Path.home() / "Downloads"
    return base / "MemuAI" / "MemuUtils"

def save_output(run: str, name: str = "", folder: str = "") -> Dict[str, Any]:
    """Copy one output (or all of a run when *name* is empty) into Downloads/MemuAI/MemuUtils (or *folder*)."""
    target_dir = Path(folder) if folder else save_folder()
    target_dir.mkdir(parents=True, exist_ok=True)
    if name:
        sources = [output_path(run, name)]
    else:
        if not RUN_RE.match(str(run or "")):
            raise UtilError("Неверный запуск", "Bad run")
        sources = sorted(p for p in (runs_dir() / run / "out").glob("*") if p.is_file())
        if not sources:
            raise UtilError("У этого запуска нет файлов", "This run has no files")
    saved = []
    for src in sources:
        dest = target_dir / src.name
        stem, suffix, n = dest.stem, dest.suffix, 2
        while dest.exists():
            dest = target_dir / f"{stem} ({n}){suffix}"
            n += 1
        shutil.copy2(src, dest)
        saved.append(str(dest))
    return {"saved": saved, "folder": str(target_dir), "path": saved[0] if len(saved) == 1 else str(target_dir)}

def reveal(path: str) -> bool:
    target = Path(path)
    if not target.exists():
        return False
    try:
        if sys.platform == "win32":
            import subprocess
            if target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)], creationflags=0x08000000)
            else:
                os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", "-R", str(target)])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(target.parent if target.is_file() else target)])
        return True
    except OSError:
        return False
