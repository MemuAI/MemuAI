# -*- coding: utf-8 -*-
"""MemuLearn operations: ``call(op, args)`` — the module bridge ``Memu.learn.call`` (action ``learn_call``, permission
``learn``), the home panel, the Mini App, and ``POST /api/pg/learn/call {op, args, module_id?, lang?}`` → ``{result}``.

Errors: :class:`LearnError` → 400/404 with a bilingual text. Everything is synchronous SQLite, run in a worker thread.
"""
from __future__ import annotations

import asyncio
import base64
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import Request

from . import ai, anki
from .store import LearnError, get_store

OPS = ("overview", "settings.get", "settings.set",
       "course.create", "course.get", "course.update", "course.delete",
       "node.get", "node.add", "node.update", "node.delete",
       "card.list", "card.get", "card.add", "card.update", "card.delete",
       "review.queue", "review.answer", "review.undo", "stats",
       "exam.list", "exam.get",
       "ai.prompt", "ai.apply",
       "source.ingest", "source.notes", "source.note",
       "import.csv", "export.csv", "import.apkg", "export.apkg",
       "panel", "katex")
MAX_UPLOAD = 60 * 1024 * 1024
_KATEX: Dict[str, str] = {}

def _data_url_bytes(value: Any) -> bytes:
    text = str(value or "")
    if text.startswith("data:"):
        text = text.split(",", 1)[1] if "," in text else ""
    try:
        raw = base64.b64decode(text, validate=False)
    except (ValueError, TypeError):
        raise LearnError("Файл повреждён", "The file is damaged")
    if not raw:
        raise LearnError("Файл пустой", "The file is empty")
    if len(raw) > MAX_UPLOAD:
        raise LearnError("Файл больше 60 МБ", "The file is larger than 60 MB", "too_large")
    return raw

def _course_view(store, cid: str) -> Dict[str, Any]:
    course = store.course(cid)
    course.update(store.course_counts(cid))
    return {"course": course, "tree": store.tree(cid), "exams": store.exams(cid)}

def _doc_summary(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {"doc_id": doc["id"], "name": doc.get("name") or "", "title": doc.get("title") or "", "pages": doc.get("approx_pages"),
            "chars": doc.get("chars"), "sections": [s.get("title") for s in (doc.get("sections") or [])[:60]]}

def _memunote_recent() -> list:
    from ..memunote.paths import note_dir, read_json
    out = []
    for item in read_json(note_dir() / "recent.json", []):
        if not isinstance(item, str):
            continue
        p = Path(item)
        if p.suffix.lower() in (".md", ".txt", ".markdown", ".docx", ".pdf", ".html", ".htm", ".rtf", ".odt", ".epub") and p.is_file():
            out.append({"path": str(p), "name": p.name, "modified": p.stat().st_mtime, "size": p.stat().st_size})
    return out[:30]

def katex_bundle() -> Dict[str, str]:
    """KaTeX for the sandboxed module window (no network, ``font-src data:``): the script and the CSS with woff2 fonts inlined."""
    if not _KATEX:
        folder = Path(__file__).resolve().parents[1] / "pgstatic" / "vendor" / "katex"
        css = (folder / "katex.min.css").read_text(encoding="utf-8")

        def font(m: "re.Match[str]") -> str:
            name = m.group(1)
            if not name.endswith(".woff2"):
                return ""
            try:
                return "url(data:font/woff2;base64," + base64.b64encode((folder / "fonts" / name).read_bytes()).decode() + ") format(\"woff2\")"
            except OSError:
                return ""
        css = re.sub(r"url\(fonts/([^)]+)\)\s*format\([\"']?[a-z0-9]+[\"']?\)", font, css)
        css = re.sub(r",\s*(?=[;}])", "", css)
        _KATEX.update(js=(folder / "katex.min.js").read_text(encoding="utf-8"), css=css)
    return dict(_KATEX)

def call(op: str, args: Optional[Dict[str, Any]] = None, *, lang: str = "ru") -> Any:
    args = args if isinstance(args, dict) else {}
    store = get_store()
    op = str(op or "")
    s = lambda key: str(args.get(key) or "")
    if op == "overview":
        return store.overview(lang)
    if op == "settings.get":
        return store.settings()
    if op == "settings.set":
        return store.set_settings(args)
    if op == "course.create":
        source = args.get("source") if isinstance(args.get("source"), dict) else {}
        kind = str(source.get("kind") or "topic")
        ref = name = ""
        if kind in ("file", "note"):
            from .. import bigdocs
            ref = str(source.get("doc_id") or "")
            doc = bigdocs.load_doc(ref) if bigdocs.valid_id(ref) else None
            if not doc:
                raise LearnError("Источник не найден — загрузите файл ещё раз", "Source not found — upload the file again", "not_found")
            name = doc.get("name") or ""
        elif kind != "topic":
            kind = "topic"
        title = s("title") or (Path(name).stem if name else "")
        course = store.create_course(title, s("description"), source_kind=kind, source_ref=ref, source_name=name,
                                     lang="en" if (s("lang") or lang) == "en" else "ru")
        return _course_view(store, course["id"])
    if op == "course.get":
        return _course_view(store, s("id"))
    if op == "course.update":
        store.update_course(s("id"), args)
        return _course_view(store, s("id"))
    if op == "course.delete":
        return store.delete_course(s("id"))
    if op == "node.add":
        return store.add_node(s("course_id"), s("title"), parent_id=s("parent_id"), kind=s("kind") or "lesson", brief=s("brief"))
    if op == "node.get":
        return store.node(s("id"))
    if op == "node.update":
        return store.update_node(s("id"), args)
    if op == "node.delete":
        return store.delete_node(s("id"))
    if op == "card.list":
        return store.cards(s("course_id"), s("lesson_id"), s("q"), int(args.get("limit") or 500), int(args.get("offset") or 0))
    if op == "card.get":
        return store.card(s("id"))
    if op == "card.add":
        return {"cards": store.add_note(s("course_id"), s("front"), s("back"), kind=s("kind") or "basic",
                                        lesson_id=s("lesson_id"), extra=s("extra"), tags=s("tags"), origin="manual")}
    if op == "card.update":
        return store.update_card(s("id"), args)
    if op == "card.delete":
        return store.delete_card(s("id"))
    if op == "review.queue":
        return store.queue(s("course_id"), int(args.get("limit") or 50), lang=lang)
    if op == "review.answer":
        return store.answer(s("id"), int(args.get("rating") or 0), duration_ms=int(args.get("duration_ms") or 0),
                            device=s("device")[:40], lang=lang)
    if op == "review.undo":
        return store.undo()
    if op == "stats":
        return store.stats(s("course_id"), max(7, min(int(args.get("days") or 30), 365)))
    if op == "exam.list":
        return store.exams(s("course_id"))
    if op == "exam.get":
        return store.exam(s("id"))
    if op == "ai.prompt":
        return ai.prompt(store, s("task"), args, lang)
    if op == "ai.apply":
        return ai.apply(store, s("task"), str(args.get("answer") or ""), args, lang)
    if op == "source.ingest":
        from .. import bigdocs
        name = s("name") or "document.txt"
        try:
            doc = bigdocs.ingest(_data_url_bytes(args.get("data")), name)
        except bigdocs.BigDocError as exc:
            raise LearnError(exc.text("ru"), exc.text("en"), getattr(exc, "code", "error"))
        return _doc_summary(doc)
    if op == "source.notes":
        return {"files": _memunote_recent()}
    if op == "source.note":
        from .. import bigdocs
        wanted = s("path")
        if wanted not in {f["path"] for f in _memunote_recent()}:
            raise LearnError("Этого файла нет среди недавних в MemuNote", "This file is not among MemuNote's recent files", "forbidden")
        try:
            return _doc_summary(bigdocs.ingest_path(wanted))
        except bigdocs.BigDocError as exc:
            raise LearnError(exc.text("ru"), exc.text("en"), getattr(exc, "code", "error"))
    if op == "import.csv":
        return anki.import_csv(store, s("course_id"), str(args.get("text") or ""), s("lesson_id"))
    if op == "export.csv":
        return anki.export_csv(store, s("course_id"))
    if op == "import.apkg":
        return anki.import_apkg(store, _data_url_bytes(args.get("data")), name=s("name"), course_id=s("course_id"), lang=lang)
    if op == "export.apkg":
        return anki.export_apkg(store, s("course_id"))
    if op == "panel":
        return panel_digest(lang)
    if op == "katex":
        return katex_bundle()
    raise LearnError(f"Неизвестная операция: {op}", f"Unknown operation: {op}", "not_found")

def panel_digest(lang: str = "ru") -> Dict[str, Any]:
    """«Повторить сегодня (N карточек · M мин)» — the home panel, the Mini App header and the AI command."""
    store = get_store()
    q = store.queue(limit=1, lang=lang)
    courses = []
    for c in store.q("SELECT id, title, hue FROM courses ORDER BY updated DESC LIMIT 6"):
        counts = store.course_counts(c["id"])
        courses.append({"id": c["id"], "title": c["title"], "hue": c["hue"], "due": counts["due"], "new": counts["new"],
                        "cards": counts["cards"], "lessons": counts["lessons"], "lessons_done": counts["lessons_done"]})
    stats = store.stats(days=7)
    return {"due": q["counts"]["total"], "counts": q["counts"], "minutes": q["minutes"], "streak": stats["streak"],
            "week": stats["by_day"], "courses": courses, "retention": stats["retention"], "at": time.time()}

def error_payload(exc: Exception, lang: str = "ru") -> tuple:
    if isinstance(exc, LearnError):
        return {"error": exc.text(lang), "code": exc.code}, 404 if exc.code == "not_found" else 403 if exc.code == "forbidden" else 400
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return {"error": str(exc)[:400] or type(exc).__name__}, 400
    return {"error": f"{exc}"[:600] or type(exc).__name__}, 500

async def call_async(op: str, args: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Any:
    return await asyncio.to_thread(call, op, args, **kwargs)

_ALLOWED: Dict[tuple, tuple] = {}
_ALLOWED_TTL = 5.0

def module_allowed(module_id: str, action: str = "learn_call") -> bool:
    """The module must declare the ``learn`` permission (answer cached for a few seconds, as MemuMusic)."""
    from ..modules_host import ACTION_PERMISSION, default_root, load_package
    root = default_root()
    key = (str(root), module_id, action)
    hit = _ALLOWED.get(key)
    if hit and time.monotonic() - hit[1] < _ALLOWED_TTL:
        return hit[0]
    try:
        package = load_package(root, module_id)
        ok = ACTION_PERMISSION.get(action, "learn") in (package.get("permissions") or [])
    except (KeyError, ValueError):
        ok = False
    _ALLOWED[key] = (ok, time.monotonic())
    return ok

def register(app, *, allowed: Optional[Callable[[str, str], bool]] = module_allowed) -> None:
    from fastapi.responses import JSONResponse

    @app.post("/api/pg/learn/call")
    async def learn_call(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "Некорректный JSON / Invalid JSON"}, 400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Ожидается JSON-объект / A JSON object is expected"}, 400)
        module_id = str(body.get("module_id") or "")
        if module_id and allowed is not None and not allowed(module_id, "learn_call"):
            return JSONResponse({"error": "Модулю не выдано право learn / The module lacks the learn permission"}, 403)
        lang = "en" if str(body.get("lang") or "") == "en" else "ru"
        try:
            result = await call_async(str(body.get("op") or ""), body.get("args") or {}, lang=lang)
        except Exception as exc:
            payload, code = error_payload(exc, lang)
            return JSONResponse(payload, code)
        return JSONResponse({"result": result})
