"""Round 35: routes and model tools of the PC-wide file search (:mod:`app.fsindex`).

* Quick Access plugin backend (``quick/plugins/index.json`` → ``"backend": "app.fsindex_api"``): :func:`register`
  adds the ``search_files`` tool for the Quick Access model and the ``/api/fsindex/*`` routes, and warms the index.
* Main chat: ``playground_api.run_tool_request`` calls :func:`chat_tool` for ``search_files``.

Routes (playground server, same-origin only — the app's local boundary middleware):
``GET  /api/fsindex/search?q=&limit=&sort=&kinds=&roots=``  → ``fsindex.search_ex`` (+ ``memufiles``: can open in MemuFiles)
``GET  /api/fsindex/status`` · ``POST /api/fsindex/settings {…}`` · ``POST /api/fsindex/rebuild``
``POST /api/fsindex/open {path, reveal?, with?: "memufiles"}`` — only a path the search returned in this process
``POST /api/fsindex/everything/start`` — runs the installed Everything in the tray (a click in Settings)
``GET  /api/fsindex/drag?path=&k=`` — the file for a drag out of the window (``DownloadURL``), key-protected
"""
from __future__ import annotations

import asyncio
import collections
import logging
import os
import secrets
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from . import fsindex

log = logging.getLogger("memuai.fsindex.api")

TOOL_DEFAULT = 30
TOOL_MAX = 100
#: paths shown to the user / the model recently — the only ones /open and /drag accept (a local page can't
#: make MemuAI open an arbitrary path it never found)
_SHOWN: "collections.OrderedDict[str, float]" = collections.OrderedDict()
_SHOWN_MAX = 5000
DRAG_KEY = secrets.token_urlsafe(18)


def _remember(rows: List[Dict[str, Any]]) -> None:
    now = time.time()
    for row in rows:
        path = row.get("path")
        if path:
            _SHOWN[os.path.normcase(path)] = now
            _SHOWN.move_to_end(os.path.normcase(path))
    while len(_SHOWN) > _SHOWN_MAX:
        _SHOWN.popitem(last=False)


def _was_shown(path: str) -> bool:
    return os.path.normcase(str(path or "")) in _SHOWN


def tr(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru


def _when(mtime: Optional[float]) -> str:
    if not mtime:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))


def _human(size: Optional[int]) -> str:
    if size is None:
        return ""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return ""


_MF_CACHE: List[Any] = [0.0, False]


def memufiles_available() -> bool:
    # Round 36: asked on every search request (every key) — the install check stats files, 15 s are plenty
    now = time.monotonic()
    if now - _MF_CACHE[0] < 15.0:
        return bool(_MF_CACHE[1])
    _MF_CACHE[:] = [now, _memufiles_check()]
    return bool(_MF_CACHE[1])


def _memufiles_check() -> bool:
    try:   # Round 35 (files): installed MemuFiles, or its source entry in a checkout
        from . import memufiles_manager
        return memufiles_manager.available()
    except Exception:
        pass
    try:
        import importlib.util
        return importlib.util.find_spec("app.memufiles") is not None
    except Exception:
        return False


# ---------------------------------------------------------------- model tool

TOOL_DESCRIPTION = (
    '{"query": "…", "limit"?: 30, "kinds"?: ["image"|"video"|"audio"|"doc"|"archive"|"exe"|"code"|"folder"], '
    '"roots"?: ["C:\\\\Projects" | "Desktop" | "Downloads"…], "sort"?: "relevance"|"date"|"size"|"name"} — find files '
    "and folders by NAME anywhere on this PC, instantly (all drives; Everything-style query: words = AND, *.txt, "
    "ext:pdf;docx, size:>100mb, dm:today | dm:7d | dm:2026-10, folder:, in:<folder>, path:<part of path>, !word; "
    "«words one | other words» = alternatives). "
    "Use it whenever the user asks where a file is or to find / list files on the computer, BEFORE saying you can't; "
    "use dm: and sort:date for «latest/recent», size: for «big files». Search by likely name words (both Russian "
    "and English variants if unsure, e.g. «отчет | report»). Results are paths; to open one use files_open (if "
    "available) or give the path."
)

PROMPT_GUIDE = (
    "search_files finds files and folders by NAME across the whole PC instantly (Everything-style query: words AND, "
    "*.ext, ext:, size:>Nmb, dm:today/7d, in:<folder>, folder:, a|b). Call it when the user asks where a file is, "
    "to find/list files, the latest downloads, big files and so on — never say you cannot search the computer."
)


def run_tool(args: Dict[str, Any], lang: str = "ru", with_ui: bool = False) -> Dict[str, Any]:
    """``search_files`` — the same for the Quick Access model and the main chat."""
    settings = fsindex.get_settings()
    if not settings.get("enabled") or not settings.get("ai_tool"):
        return {"error": tr(lang, "Поиск файлов по ПК выключен в настройках (Быстрый доступ → Поиск файлов).",
                            "PC-wide file search is turned off in Settings (Quick Access → File search).")}
    query = str(args.get("query") or args.get("q") or args.get("name") or "").strip()
    if not query:
        return {"error": "query is required"}
    try:
        limit = max(1, min(TOOL_MAX, int(args.get("limit") or TOOL_DEFAULT)))
    except (TypeError, ValueError):
        limit = TOOL_DEFAULT
    kinds = args.get("kinds") or args.get("kind") or None
    roots = args.get("roots") or args.get("root") or args.get("folder") or None
    sort = str(args.get("sort") or "") or None
    got = fsindex.search_ex(query, limit=limit, kinds=kinds, roots=roots, sort=sort, timeout=1.5)
    if got.get("error") and not got.get("results"):
        code = got.get("code") or ""
        if code == "indexing":
            msg = tr(lang, "Индекс файлов ещё строится (первый запуск занимает до пары минут). Попробуйте чуть позже "
                           "или установите Everything — тогда поиск мгновенный сразу.",
                     "The file index is still being built (the first run takes up to a couple of minutes). Try again "
                     "shortly, or install Everything for instant search right away.")
            return {"error": msg, "code": code, "query": query}
        return {"error": str(got.get("error")), "code": code, "query": query}
    rows = got.get("results") or []
    _remember(rows)
    out: Dict[str, Any] = {
        "query": query, "backend": got.get("backend"), "count": len(rows), "total": got.get("total"),
        "more": bool(got.get("capped")) or (got.get("total") or 0) > len(rows),
        "note_paths": "Mention files by their exact full path from this list (never shorten or invent a path).",
        "results": [{"path": r["path"], "type": "folder" if r.get("is_dir") else "file",
                     **({"size": _human(r.get("size"))} if not r.get("is_dir") and r.get("size") is not None else {}),
                     **({"modified": _when(r.get("mtime"))} if r.get("mtime") else {})} for r in rows],
    }
    if got.get("building"):
        out["note"] = "the index is still being built — the list may be incomplete"
    if not rows:
        out["hint"] = ("Nothing found. Try other name words (synonyms, the other language, fewer words), a wildcard "
                       "(*part*), or drop filters.")
    if with_ui and rows:
        out["ui"] = {"type": "fsresults", "query": query, "backend": got.get("backend"),
                     "total": got.get("total"), "more": out["more"],
                     "results": [{k: r.get(k) for k in ("path", "name", "dir", "size", "mtime", "is_dir", "kind")}
                                 for r in rows[:12]]}
    return out


async def chat_tool(args: Dict[str, Any], body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    lang = "en" if str((body or {}).get("lang") or "") == "en" else "ru"
    result = await asyncio.to_thread(run_tool, args if isinstance(args, dict) else {}, lang)
    return {"tool": "search_files", **result}


def _tool_enabled(_settings: Dict[str, Any]) -> bool:
    try:
        s = fsindex.get_settings()
        return bool(s.get("enabled") and s.get("ai_tool"))
    except Exception:
        return False


async def _quick_handler(args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    lang = (ctx or {}).get("lang") or "ru"
    return await asyncio.to_thread(run_tool, args if isinstance(args, dict) else {}, lang, True)


def _label(args: Any, lang: str) -> str:
    q = str((args or {}).get("query") or "") if isinstance(args, dict) else ""
    q = q[:40] + ("…" if len(q) > 40 else "")
    return tr(lang, f"Ищу файлы: {q}", f"Searching files: {q}") if q else tr(lang, "Ищу файлы…", "Searching files…")


def register_tools() -> None:
    try:
        from .quick_ask_api import register_tool
        register_tool("search_files", TOOL_DESCRIPTION, _quick_handler, label=_label, enabled=_tool_enabled)
    except ValueError:
        pass
    except Exception as exc:
        log.debug("fsindex: quick tool not registered: %s", exc)


# ---------------------------------------------------------------- opening

def open_path(path: str, reveal: bool = False) -> bool:
    if reveal and sys.platform == "win32":
        from . import fsindex_win
        return fsindex_win.reveal(path)
    try:
        from . import quick_files
        return bool(quick_files.open_path(path, reveal))
    except Exception:
        pass
    target = os.path.dirname(path) if reveal else path
    try:
        if sys.platform == "win32":
            os.startfile(target)          # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def open_in_memufiles(path: str) -> bool:
    """Ask MemuFiles to show the item (its own launcher/CLI when the files zone provides one)."""
    try:
        from .memufiles import desktop as mf_desktop     # type: ignore
        for name in ("open_path", "reveal", "open_in_memufiles", "launch"):
            fn = getattr(mf_desktop, name, None)
            if callable(fn):
                fn(path)
                return True
    except Exception as exc:
        log.debug("fsindex: MemuFiles open failed: %s", exc)
    if getattr(sys, "frozen", False):
        exe = os.path.join(os.path.dirname(sys.executable), "MemuFiles.exe")
        if os.path.isfile(exe):
            try:
                subprocess.Popen([exe, path], close_fds=True)
                return True
            except OSError:
                return False
    return False


# ---------------------------------------------------------------- routes

router = APIRouter()


def _lang(request: Request, body: Optional[Dict[str, Any]] = None) -> str:
    value = (body or {}).get("lang") or request.query_params.get("lang")
    return "en" if value == "en" else "ru"


async def _body(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


@router.get("/api/fsindex/search")
async def api_search(request: Request):
    q = request.query_params
    text = str(q.get("q") or "")
    try:
        limit = max(1, min(500, int(q.get("limit") or 60)))
    except ValueError:
        limit = 60
    kinds = [k for k in str(q.get("kinds") or "").split(",") if k.strip()] or None
    roots = [r for r in str(q.get("roots") or "").split("|") if r.strip()] or None
    if not fsindex.get_settings().get("enabled"):
        return {"results": [], "error": "disabled", "code": "disabled", "query": text}
    got = await asyncio.to_thread(fsindex.search_ex, text, limit, kinds, roots, q.get("sort") or None)
    _remember(got.get("results") or [])
    got["memufiles"] = memufiles_available()
    got["drag_key"] = DRAG_KEY
    got["windows"] = sys.platform == "win32"     # Round 36: the menu offers «Открыть с помощью» / file copy only there
    return got


@router.get("/api/fsindex/status")
async def api_status():
    st = await asyncio.to_thread(fsindex.status)
    st["memufiles"] = memufiles_available()
    st["windows"] = sys.platform == "win32"
    return st


@router.post("/api/fsindex/warm")
async def api_warm():
    """The file search panel was opened: load / build the own index now (lazy since round 36), cheap when it is up."""
    if fsindex.get_settings().get("enabled"):
        await asyncio.to_thread(fsindex.start)
    return {"ok": True}


@router.post("/api/fsindex/settings")
async def api_settings(request: Request):
    body = await _body(request)
    patch = body.get("settings") if isinstance(body.get("settings"), dict) else body
    patch = {k: v for k, v in patch.items() if k != "lang"}
    settings = await asyncio.to_thread(fsindex.save_settings, patch)
    return {"ok": True, "settings": settings}


@router.post("/api/fsindex/rebuild")
async def api_rebuild():
    await asyncio.to_thread(fsindex.rebuild)
    return {"ok": True}


@router.post("/api/fsindex/open")
async def api_open(request: Request):
    body = await _body(request)
    lang = _lang(request, body)
    path = str(body.get("path") or "")
    if not path or not _was_shown(path):
        return JSONResponse({"error": tr(lang, "Этот путь не из результатов поиска", "This path is not a search result")}, 400)
    if not os.path.lexists(path):
        return JSONResponse({"error": tr(lang, "Файл уже удалён или перемещён", "The file was deleted or moved")}, 404)
    if body.get("with") == "memufiles":
        ok = await asyncio.to_thread(open_in_memufiles, path)
        if not ok:
            return JSONResponse({"error": tr(lang, "MemuFiles не установлен", "MemuFiles is not installed")}, 404)
        return {"ok": True}
    ok = await asyncio.to_thread(open_path, path, bool(body.get("reveal")))
    return {"ok": ok}


@router.post("/api/fsindex/everything/start")
async def api_everything_start():
    if sys.platform != "win32":
        return JSONResponse({"error": "Windows only"}, 400)
    from . import fsindex_win
    ok = await asyncio.to_thread(fsindex_win.start_everything)
    fsindex.service()._everything_cache = (0.0, None)
    return {"ok": ok}


@router.get("/api/fsindex/drag")
async def api_drag(request: Request):
    """The file behind a drag out of the window: Chromium's ``DownloadURL`` makes Explorer copy it from here."""
    path = str(request.query_params.get("path") or "")
    if not secrets.compare_digest(str(request.query_params.get("k") or ""), DRAG_KEY) or not _was_shown(path):
        return JSONResponse({"error": "forbidden"}, 403)
    if not os.path.isfile(path):
        return JSONResponse({"error": "not a file"}, 404)
    return FileResponse(path, filename=os.path.basename(path))


# Round 36: the right-click menu of the results (rename, duplicate, ZIP, Recycle Bin…) — app/fsindex_ops.py
from . import fsindex_ops  # noqa: E402

router.routes.extend(fsindex_ops.router.routes)     # flat (quick_ask_api._include re-orders plain routes)


def register() -> None:
    """Quick Access plugin backend: tool + routes + a warm index (in the background)."""
    register_tools()
    try:
        from .quick_ask_api import register_router
        register_router(router)
    except Exception as exc:
        log.debug("fsindex: routes not registered: %s", exc)
    if os.environ.get("MEMUAI_FSINDEX_NOSTART"):
        return

    def warm() -> None:
        # after the app has started (no disk storm at launch); a saved index loads in about a second.
        # Round 36: only with «preload» on — otherwise the index (~90 MB per million files, a full disk walk when
        # there is no saved one) is loaded on the first file search / opening of the search panel (/api/fsindex/warm).
        time.sleep(WARM_DELAY)
        try:
            settings = fsindex.get_settings()
            if settings.get("enabled") and settings.get("preload"):
                fsindex.start()
        except Exception as exc:
            log.warning("fsindex: warm start failed: %s", exc)
    import threading
    threading.Thread(target=warm, name="fsindex-warm", daemon=True).start()


WARM_DELAY = 8.0
_REGISTERED = []
if not _REGISTERED:
    _REGISTERED.append(True)
    register()
