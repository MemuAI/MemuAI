"""Round 29: more Quick Access helpers (plugin ``explorer`` → ``pgstatic/quick/plugins/extras.js``).

* **Currency** — «100 usd в rub», «€50 → $» under the input, no model: rates of ``open.er-api.com``
  (fallback ``api.frankfurter.app``), cached 6 h in ``memuai/quick_rates.json``; the page parses the text.
* **Text from the screen without a model** — a region (the Round 27 overlay) read by Windows OCR
  (``Windows.Media.Ocr`` through PowerShell, languages installed in Windows).
* **Eyedropper** — the colour under the pointer at Alt+Space (captured with the app context) and a live
  pipette (``GET …/color/now`` polled while the user points; Space takes it).
* **Quick note** — a line appended to ``<Documents>/MemuAI Notes/Quick notes.md`` (a dated heading per day),
  opened in MemuNote when it is installed (``app.memunote.desktop.hand_off``) or with the default program.
* **Latest downloads** — the newest files of the Downloads folder; one click attaches the file as a folder
  context (the folder agent of :mod:`app.quick_explorer` then reads / renames / sorts it).

Routes: ``/api/quick/extras/*`` (registered by :func:`register` from :mod:`app.quick_explorer`).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import quick_apps
from .quick_apps import tr

log = logging.getLogger("memuai.quick.extras")

RATES_TTL = 6 * 3600
RATE_SOURCES = (
    ("open.er-api.com", "https://open.er-api.com/v6/latest/{base}"),
    ("frankfurter.app", "https://api.frankfurter.app/latest?from={base}"),
)
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
NOTE_MAX = 4000
OCR_MAX_CHARS = 14_000_000

def _rates_file() -> Path:
    return quick_apps._memu_dir() / "quick_rates.json"

def parse_rates(source: str, payload: Any) -> Dict[str, float]:
    """``{"EUR": 0.92, …}`` from either provider's JSON (``{}`` when it is not usable)."""
    if not isinstance(payload, dict):
        return {}
    raw = payload.get("rates")
    if source.startswith("open.er-api") and payload.get("result") not in (None, "success"):
        return {}
    out: Dict[str, float] = {}
    for code, value in (raw.items() if isinstance(raw, dict) else []):
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if CURRENCY_RE.match(str(code)) and number > 0 and number == number:
            out[str(code)] = number
    return out

def load_rates(base: str = "USD", *, fetch=None, now: Optional[float] = None) -> Dict[str, Any]:
    """Cached rates for ``base`` → ``{"base", "rates", "source", "t", "stale"?}`` or ``{"error"}``."""
    base = str(base or "USD").upper()
    if not CURRENCY_RE.match(base):
        return {"error": "bad currency"}
    now = time.time() if now is None else now
    cache: Dict[str, Any] = {}
    try:
        cache = json.loads(_rates_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    row = cache.get(base) if isinstance(cache, dict) else None
    if isinstance(row, dict) and now - float(row.get("t") or 0) < RATES_TTL and row.get("rates"):
        return {"base": base, **row}
    fetch = fetch or _http_json
    for name, template in RATE_SOURCES:
        try:
            rates = parse_rates(name, fetch(template.format(base=base)))
        except Exception as exc:
            log.debug("rates from %s failed: %s", name, exc)
            continue
        if rates:
            rates[base] = 1.0
            fresh = {"rates": rates, "source": name, "t": now}
            cache = cache if isinstance(cache, dict) else {}
            cache[base] = fresh
            try:
                path = _rates_file()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(cache), encoding="utf-8")
            except OSError:
                pass
            return {"base": base, **fresh}
    if isinstance(row, dict) and row.get("rates"):
        return {"base": base, **row, "stale": True}
    return {"error": "offline"}

def _http_json(url: str) -> Any:
    import httpx
    with httpx.Client(timeout=6.0, follow_redirects=False) as client:
        response = client.get(url, headers={"User-Agent": "MemuAI"})
        response.raise_for_status()
        return response.json()

def convert(amount: float, src: str, dst: str, rates: Dict[str, float]) -> Optional[float]:
    """``amount`` of ``src`` in ``dst`` with rates of any base (cross through the base)."""
    src, dst = src.upper(), dst.upper()
    if src not in rates or dst not in rates or rates[src] <= 0:
        return None
    return amount / rates[src] * rates[dst]

def notes_file(settings: Optional[Dict[str, Any]] = None, lang: str = "ru") -> Path:
    settings = settings or quick_apps.settings()
    folder = str(settings.get("notes_dir") or "").strip()
    base = Path(folder) if folder and os.path.isabs(folder) else Path(quick_apps.documents_folder()) / "MemuAI Notes"
    own, other = ("Quick notes.md", "Быстрые заметки.md") if lang == "en" else ("Быстрые заметки.md", "Quick notes.md")
    if not (base / own).is_file() and (base / other).is_file():
        return base / other
    return base / own

def add_note(text: str, *, lang: str = "ru", settings: Optional[Dict[str, Any]] = None,
             now: Optional[float] = None) -> Dict[str, Any]:
    body = str(text or "").replace("\r\n", "\n").strip()[:NOTE_MAX]
    if not body:
        return {"error": tr(lang, "Пустая заметка", "Empty note")}
    path = notes_file(settings, lang)
    stamp = time.localtime(time.time() if now is None else now)
    day = time.strftime("%Y-%m-%d", stamp)
    head = f"## {day}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_bytes().decode("utf-8-sig", errors="replace") if path.is_file() else ""
    except OSError as exc:
        return {"error": tr(lang, f"Не удалось открыть файл заметок: {exc}", f"Could not open the notes file: {exc}")}
    eol = "\r\n" if "\r\n" in existing else "\n"
    existing = existing.replace("\r\n", "\n")
    lines = body.split("\n")
    entry = f"- {time.strftime('%H:%M', stamp)} {lines[0]}" + "".join(f"\n  {line}" for line in lines[1:])
    chunk = ""
    if not existing:
        chunk = ("# Быстрые заметки\n" if lang != "en" else "# Quick notes\n")
    if head not in existing.split("\n"):
        chunk += ("\n" if existing and not existing.endswith("\n\n") else "") + head + "\n"
    elif existing and not existing.endswith("\n"):
        chunk += "\n"
    chunk += entry + "\n"
    try:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(chunk.replace("\n", eol))
    except OSError as exc:
        return {"error": tr(lang, f"Не удалось сохранить заметку: {exc}", f"Could not save the note: {exc}")}
    return {"ok": True, "path": str(path), "name": path.name}

def open_notes(path: Path) -> Dict[str, Any]:
    """Open the notes file in MemuNote (running instance → hand-off, installed exe → launch) or the default app."""
    if not path.is_file():
        return {"ok": False, "error": "missing"}
    try:
        from .memunote import desktop as memunote_desktop
        if memunote_desktop.hand_off([str(path)]):
            return {"ok": True, "app": "MemuNote"}
    except Exception:
        pass
    try:
        from . import memunote_manager
        if memunote_manager.open_app([str(path)]).get("ok"):
            return {"ok": True, "app": "MemuNote"}
    except Exception:
        pass
    if quick_apps.IS_WINDOWS:
        from . import quick_apps_win
        return {"ok": quick_apps_win.open_path(str(path)), "app": ""}
    return {"ok": False, "native": False}

PARTIAL = (".crdownload", ".part", ".partial", ".download", ".tmp", ".opdownload")
DOWNLOAD_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                        ".gif": "image/gif", ".bmp": "image/bmp"}
DOWNLOAD_IMAGE_LIMIT = 12 * 1024 * 1024

def latest_downloads(folder: str = "", limit: int = 8) -> List[Dict[str, Any]]:

    raw = str(folder or quick_apps.downloads_folder() or "").strip()
    if not raw or not os.path.isabs(raw):
        return []
    base = Path(raw)
    if not base.is_dir():
        return []
    rows = []
    try:
        for entry in os.scandir(base):
            if not entry.is_file(follow_symlinks=False):
                continue
            low = entry.name.lower()
            if low.endswith(PARTIAL) or low in ("desktop.ini", "thumbs.db") or low.startswith("~$"):
                continue
            try:
                st = entry.stat()
            except OSError:
                continue
            rows.append({"name": entry.name, "size": int(st.st_size), "mtime": int(st.st_mtime)})
    except OSError:
        return []
    rows.sort(key=lambda r: -r["mtime"])
    return rows[:max(1, min(30, limit))]

def downloads_context(name: str, lang: str = "ru") -> Dict[str, Any]:
    base = quick_apps.downloads_folder()
    if not base:
        return {"error": tr(lang, "Папка «Загрузки» не найдена", "The Downloads folder was not found")}
    clean = os.path.basename(str(name or ""))
    path = Path(base) / clean
    if not clean or not path.is_file() or path.is_symlink():
        return {"error": tr(lang, "Файл не найден", "File not found")}
    ctx = quick_apps.register_context({"kind": "explorer", "app": "Explorer", "source": "downloads", "exe": "explorer.exe",
                                       "title": Path(base).name, "folder": base, "selected": [str(path)]})
    out: Dict[str, Any] = {"context": quick_apps.public_context(ctx, lang)}

    mime = DOWNLOAD_IMAGE_TYPES.get(path.suffix.lower())
    if mime:
        try:
            size = path.stat().st_size
            if 0 < size <= DOWNLOAD_IMAGE_LIMIT:
                out["image"] = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")
            else:
                out["image_error"] = tr(lang, "Картинка больше 12 МБ — прикреплена как файл",
                                        "The image is over 12 MB — attached as a file")
        except OSError:
            pass
    return out

def ocr_image(data_url: str, lang: str = "ru") -> Dict[str, Any]:
    match = re.match(r"^data:image/(png|jpe?g|bmp|gif|webp);base64,(.+)$", str(data_url or ""), re.S)
    if not match or len(data_url) > OCR_MAX_CHARS:
        return {"error": tr(lang, "Нужна картинка", "An image is needed"), "code": "bad_image"}
    if not quick_apps.IS_WINDOWS:
        return {"error": tr(lang, "Распознавание без модели работает в приложении MemuAI для Windows",
                            "Recognition without a model works in the MemuAI Windows app"), "code": "unsupported"}
    raw = base64.b64decode(match.group(2), validate=False)
    suffix = ".jpg" if match.group(1).startswith("jp") else "." + match.group(1)
    handle, path = tempfile.mkstemp(prefix="memuai-ocr-", suffix=suffix)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(raw)
        from . import quick_apps_win
        result = quick_apps_win.windows_ocr(path, "ru" if lang == "ru" else "")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if result.get("error") == "no_engine":
        return {"error": tr(lang, "Windows OCR недоступно: добавьте язык с распознаванием текста в Параметры → Время и язык",
                            "Windows OCR is unavailable: add a language with OCR in Settings → Time & language"),
                "code": "ocr"}
    if result.get("error"):

        return {"error": tr(lang, "Не удалось распознать текст на картинке — попробуйте ещё раз или выделите область крупнее",
                            "Could not recognise text in the image — try again or select a larger area"),
                "code": "ocr_failed"}
    return {"text": str(result.get("text") or "").strip(), "lang": result.get("lang") or "", "lines": result.get("lines") or 0}

router = APIRouter()

def _lang(body: Dict[str, Any], request: Optional[Request] = None) -> str:
    lang = str((body or {}).get("lang") or (request.query_params.get("lang") if request is not None else "") or "")
    return lang if lang in ("ru", "en") else "ru"

async def _json(request: Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}

@router.get("/api/quick/extras/rates")
async def extras_rates(request: Request):
    base = str(request.query_params.get("base") or "USD").upper()
    result = await asyncio.to_thread(load_rates, base)
    if result.get("error"):
        lang = _lang({}, request)
        return JSONResponse({"error": tr(lang, "Курсы валют недоступны (нет сети?)", "Exchange rates unavailable (offline?)"),
                             "code": result["error"]}, 503)
    return result

@router.post("/api/quick/extras/ocr")
async def extras_ocr(request: Request):
    body = await _json(request)
    lang = _lang(body)
    result = await asyncio.to_thread(ocr_image, str(body.get("data") or ""), lang)
    return JSONResponse(result, 400) if result.get("error") else result

@router.get("/api/quick/extras/color/now")
async def extras_color_now(request: Request):
    if not quick_apps.IS_WINDOWS:
        return JSONResponse({"error": "unsupported", "code": "unsupported"}, 400)
    from . import quick_apps_win
    color = await asyncio.to_thread(quick_apps_win.pixel_under_cursor)
    return {"color": color}

@router.post("/api/quick/extras/note")
async def extras_note(request: Request):
    body = await _json(request)
    lang = _lang(body)
    if not quick_apps.settings().get("notes", True):
        return JSONResponse({"error": tr(lang, "Быстрые заметки выключены", "Quick notes are off")}, 400)
    result = await asyncio.to_thread(add_note, str(body.get("text") or ""), lang=lang)
    if result.get("error"):
        return JSONResponse(result, 400)
    if body.get("open"):
        result["opened"] = (await asyncio.to_thread(open_notes, Path(result["path"]))).get("ok", False)
    return result

@router.post("/api/quick/extras/note/open")
async def extras_note_open(request: Request):
    body = await _json(request)
    lang = _lang(body)
    path = notes_file(None, lang)
    if not path.is_file():
        return JSONResponse({"error": tr(lang, "Заметок ещё нет", "No notes yet")}, 404)
    return await asyncio.to_thread(open_notes, path)

@router.get("/api/quick/extras/downloads")
async def extras_downloads(request: Request):
    base = await asyncio.to_thread(quick_apps.downloads_folder)
    rows = await asyncio.to_thread(latest_downloads, base) if base else []
    return {"items": rows, "folder": os.path.basename(base or ""), "found": bool(base)}

@router.post("/api/quick/extras/downloads/use")
async def extras_downloads_use(request: Request):
    body = await _json(request)
    lang = _lang(body)
    result = await asyncio.to_thread(downloads_context, str(body.get("name") or ""), lang)
    return JSONResponse(result, 400) if result.get("error") else result

def register() -> None:
    from .quick_ask_api import register_router
    register_router(router)
