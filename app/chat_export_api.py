# -*- coding: utf-8 -*-
"""Round 29: routes of the chat export (``/api/export/*``) for the MemuAI window.

* ``GET  /api/export/capabilities`` — which PDF engine is available (headless Edge or the print dialog).
* ``POST /api/export/render``       — build PDF / DOCX / HTML (or save MD / JSON / TXT) and save it.
* ``POST /api/export/open``         — open a file this window exported (only those, nothing else).

Files go to ``Downloads/MemuAI`` (the same place as every other MemuAI download) or, when the user
chose «Ask where to save», through the native Save dialog of the window.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Set

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import chat_export as ce

log = logging.getLogger("memuai.export")
_SAVED: Set[str] = set()
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
MIME = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "html": "text/html", "md": "text/markdown", "json": "application/json", "txt": "text/plain"}
FILE_TYPES = {"pdf": "PDF (*.pdf)", "docx": "Word (*.docx)", "html": "HTML (*.html)", "md": "Markdown (*.md)",
              "json": "JSON (*.json)", "txt": "Text (*.txt)"}


def downloads_dir() -> Path:
    base = Path.home() / "Downloads" / "MemuAI"
    base.mkdir(parents=True, exist_ok=True)
    return base


def unique_path(folder: Path, name: str) -> Path:
    target = folder / name
    stem, suffix, index = target.stem, target.suffix, 2
    while target.exists():
        target = folder / f"{stem} ({index}){suffix}"
        index += 1
    return target


async def save_bytes(app: FastAPI, data: bytes, name: str, fmt: str, mode: str) -> Dict[str, Any]:
    target: Optional[Path] = None
    window = getattr(app.state, "window", None)
    if mode == "ask" and window is not None:
        try:
            import webview as _wv
            picked = await asyncio.to_thread(window.create_file_dialog, _wv.SAVE_DIALOG, save_filename=name,
                                             file_types=(FILE_TYPES.get(fmt, "All files (*.*)"), "All files (*.*)"))
        except TypeError:
            import webview as _wv
            picked = await asyncio.to_thread(window.create_file_dialog, _wv.SAVE_DIALOG, save_filename=name)
        except Exception as exc:  # noqa: BLE001
            log.info("export: save dialog failed (%s)", type(exc).__name__)
            picked = None
        else:
            if not picked:
                return {"ok": False, "cancelled": True}
        chosen = str((picked or [""])[0] if isinstance(picked, (list, tuple)) else (picked or "")).strip()
        if chosen:
            target = Path(chosen)
            if not target.suffix:
                target = target.with_suffix("." + fmt)
    if target is None:
        target = unique_path(downloads_dir(), name)
    if target.stem.split(".", 1)[0].casefold() in _RESERVED:
        target = target.with_name("_" + target.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(target.write_bytes, data)
    _SAVED.add(os.path.normcase(str(target.resolve())))
    return {"ok": True, "path": str(target), "name": target.name, "bytes": len(data)}


def _replace_rasters(body: str, shots: Dict[str, str]) -> str:
    """Swap each ``data-mx-raster="id"`` element for its PNG snapshot (DOCX)."""
    if not shots:
        return body
    root = ce.parse_html(body)

    def walk(node: ce.Node) -> None:
        for index, child in enumerate(list(node.children)):
            if not isinstance(child, ce.Node):
                continue
            ident = child.attrs.get("data-mx-raster")
            if ident and ident in shots:
                image = ce.Node("img", {"src": shots[ident], "class": "mx-block-img", "data-scale": "2",
                                        "alt": child.attrs.get("data-mx-alt", "")}, node)
                node.children[index] = image
                continue
            walk(child)

    walk(root)
    return serialize(root)


def serialize(node: ce.Node) -> str:
    import html as _html
    parts = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(_html.escape(child, quote=False))
            continue
        attrs = "".join(f' {key}="{_html.escape(value, quote=True)}"' for key, value in child.attrs.items())
        if child.tag in ce.VOID:
            parts.append(f"<{child.tag}{attrs}>")
        else:
            parts.append(f"<{child.tag}{attrs}>{serialize(child)}</{child.tag}>")
    return "".join(parts)


def register(app: FastAPI) -> None:
    @app.get("/api/export/capabilities")
    async def export_capabilities():
        from . import chat_export_pdf
        edge = await asyncio.to_thread(chat_export_pdf.available)
        return {"pdf": "edge" if edge else "print", "docx": True, "raster": edge,
                "formats": list(ce.FORMATS), "downloads": str(Path.home() / "Downloads" / "MemuAI")}

    @app.get("/api/export/katex.css")
    async def export_katex_css():
        from fastapi.responses import Response
        css = await asyncio.to_thread(ce.katex_inline_css)
        return Response(css, media_type="text/css", headers={"Cache-Control": "max-age=3600"})

    @app.post("/api/export/render")
    async def export_render(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "bad request"}, 400)
        fmt = str(body.get("format") or "").lower()
        if fmt not in ce.FORMATS:
            return JSONResponse({"error": "unknown format"}, 400)
        options = body.get("options") if isinstance(body.get("options"), dict) else {}
        name = ce.safe_filename(str(body.get("filename") or options.get("title") or "MemuAI chat"), fmt)
        mode = "ask" if body.get("save") == "ask" else "downloads"
        lang = "en" if options.get("lang") == "en" else "ru"
        document = str(body.get("html") or "")
        if len(document) > ce.MAX_HTML_CHARS or len(str(body.get("body") or "")) > ce.MAX_HTML_CHARS:
            return JSONResponse({"error": "The chat is too large to export" if lang == "en" else "Чат слишком большой для экспорта"}, 413)
        try:
            if fmt == "pdf":
                from . import chat_export_pdf
                if not await asyncio.to_thread(chat_export_pdf.available):
                    return JSONResponse({"error": "Microsoft Edge was not found" if lang == "en" else "Microsoft Edge не найден",
                                         "fallback": "print"}, 501)
                document = await asyncio.to_thread(ce.finalize_html, document, embed_katex=True)
                data = await chat_export_pdf.html_to_pdf(document, options)
                if not data.startswith(b"%PDF"):
                    raise RuntimeError("PDF engine returned no document")
            elif fmt == "docx":
                fragment = str(body.get("body") or "")
                if body.get("raster") and document:
                    from . import chat_export_pdf
                    if await asyncio.to_thread(chat_export_pdf.available):
                        try:
                            shots = await chat_export_pdf.rasterize(
                                await asyncio.to_thread(ce.finalize_html, document, embed_katex=True),
                                width=int(options.get("viewport") or 820))
                            fragment = await asyncio.to_thread(_replace_rasters, fragment, shots)
                        except Exception as exc:  # noqa: BLE001
                            log.info("export: raster pass failed (%s)", type(exc).__name__)
                data = await asyncio.to_thread(ce.html_to_docx, fragment, {**options, "lang": lang})
            elif fmt == "html":
                data = (await asyncio.to_thread(ce.finalize_html, document, embed_katex=True)).encode("utf-8")
            else:
                if body.get("data"):
                    data = base64.b64decode(str(body["data"]))
                else:
                    data = str(body.get("text") or "").encode("utf-8")
        except Exception as exc:  # noqa: BLE001
            log.warning("export: %s failed (%s)", fmt, type(exc).__name__)
            message = str(exc)[:300] or type(exc).__name__
            return JSONResponse({"error": message, "fallback": "print" if fmt == "pdf" else ""}, 500)
        saved = await save_bytes(app, data, name, fmt, mode)
        return saved

    @app.post("/api/export/open")
    async def export_open(request: Request):
        try:
            body = await request.json()
            target = Path(str(body.get("path") or "")).resolve()
        except Exception:
            return JSONResponse({"error": "bad request"}, 400)
        key = os.path.normcase(str(target))
        if key not in _SAVED or not target.is_file():
            return JSONResponse({"error": "not an exported file"}, 403)
        try:
            if os.name == "nt":
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)[:200]}, 500)
        return {"ok": True}
