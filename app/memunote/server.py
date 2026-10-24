"""The local HTTP backend of MemuNote (stdlib ``ThreadingHTTPServer``, no FastAPI).

Why not js_api: a 100 MB file must not travel through the WebView2 message
bridge as one JSON string. The page fetches raw bytes (``/api/raw``) and decodes
them with ``TextDecoder``; saving posts the UTF-8 text body straight to
``/api/save_text``. Import cost of this module is ~5 ms.

Security: binds ``127.0.0.1`` on a random port; every ``/api`` call needs the
per-process token (``X-MN-Token`` header, or ``t=`` for ``<img>``/pdf.js GETs);
the ``Host`` header must be the loopback address (DNS-rebinding guard); static
files are served only from ``static/``. File access is the user's own (this is
a desktop editor); raw reads are limited to files the page opened.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import APP_NAME, SERVICE
from . import settings as settings_store
from .paths import memuai_preferences, note_dir, read_json, recovery_dir, write_json

log = logging.getLogger("memunote.server")

STATIC = Path(__file__).resolve().parent / "static"
MAX_JSON = 64 * 1024 * 1024
MAX_TEXT = 1024 * 1024 * 1024
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".avif"}

KINDS = {
    "markdown": {".md", ".markdown", ".mdown", ".mkd", ".mkdn", ".mdx", ".rmd"},
    "docx": {".docx", ".docm", ".dotx"},
    "odt": {".odt", ".ott"},
    "rtf": {".rtf"},
    "pdf": {".pdf"},
    "image": IMAGE_EXT | {".tif", ".tiff", ".psd", ".psb", ".heic", ".heif", ".memuimg", ".jfif", ".tga", ".pcx", ".ppm", ".pgm", ".pbm", ".pnm", ".dds", ".apng", ".cur", ".icns"},   # Round 32: photo editor
    "csv": {".csv", ".tsv", ".tab"},
    "json": {".json", ".jsonc", ".json5", ".geojson", ".jsonl", ".ndjson", ".webmanifest"},
    "log": {".log", ".out", ".err"},
    "html": {".html", ".htm", ".xhtml"},
    # Round 31: spreadsheets (read-only table), e-books (reader), Jupyter notebooks (rendered view + JSON text)
    "sheet": {".xlsx", ".xlsm", ".ods"},
    "book": {".epub", ".fb2"},
    "notebook": {".ipynb"},
    # Round 32 (slides): presentations — a full editor (static/js/slides/*); .odp is import-only (saved as .pptx)
    "slides": {".pptx", ".pptm", ".ppsx", ".potx", ".odp"},
    "legacy": {".doc", ".xls", ".ppt", ".pages"},
}
RICH_KINDS = ("docx", "odt", "rtf")
RICH_WRITERS = ("docx", "odt", "rtf", "html", "md", "txt")


def kind_of(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    for kind, exts in KINDS.items():
        if ext in exts:
            return kind
    return "text"


def _stat(path: str) -> Dict[str, Any]:
    try:
        st = os.stat(path)
        return {"exists": True, "size": st.st_size, "mtime": st.st_mtime_ns // 1_000_000,
                "readonly": not os.access(path, os.W_OK)}
    except OSError:
        return {"exists": False, "size": 0, "mtime": 0, "readonly": False}


def _clean_path(value: Any) -> str:
    text = str(value or "").strip().strip('"')
    if not text or "\x00" in text or len(text) > 4096:
        raise ValueError("bad path")
    text = os.path.expandvars(os.path.expanduser(text))
    if not os.path.isabs(text):
        raise ValueError("path must be absolute")
    return os.path.normpath(text)


class Events:
    """Long-poll event queue (second instance opened files, shutdown, drag & drop)."""

    def __init__(self) -> None:
        self.items: List[Tuple[int, Dict[str, Any]]] = []
        self.seq = 0
        self.delivered = 0      # last seq some page received: a reloaded page must not replay old "open"s
        self.cond = threading.Condition()

    def push(self, event: Dict[str, Any]) -> None:
        with self.cond:
            self.seq += 1
            self.items.append((self.seq, event))
            self.items = self.items[-200:]
            self.cond.notify_all()

    def wait(self, since: int, timeout: float = 25.0) -> Tuple[int, List[Dict[str, Any]]]:
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                fresh = [e for s, e in self.items if s > since]
                if fresh or time.monotonic() >= deadline:
                    if fresh:
                        self.delivered = max(self.delivered, self.seq)
                    return self.seq, fresh
                self.cond.wait(max(0.05, deadline - time.monotonic()))


class NoteApp:
    """State shared by request handlers; ``desktop`` is the window glue (None in tests/dev)."""

    def __init__(self, files: Optional[List[str]] = None) -> None:
        self.token = secrets.token_urlsafe(24)
        self.events = Events()
        self.opened: set = set()
        self.initial_files = [f for f in (files or []) if f]
        self.desktop: Any = None
        self.streams: Dict[str, Dict[str, Any]] = {}
        self.port = 0
        self.started = time.time()
        # "starting" → "window" (pywebview shown) | "browser" (no WebView2) | "headless" (--no-window).
        self.ui = "starting"
        self.last_seen = 0.0                 # monotonic time of the page's last /api/events poll
        self.stopping = threading.Event()    # set by a remote shutdown when there is no window to close

    # ---------------------------------------------------------------- helpers
    def take_initial_files(self) -> List[str]:
        """The command-line files, once: a reloaded page must not reopen tabs the user has closed since."""
        files, self.initial_files = self.initial_files, []
        return files

    def allow(self, path: str) -> None:
        self.opened.add(os.path.normcase(os.path.abspath(path)))

    def allowed(self, path: str) -> bool:
        return os.path.normcase(os.path.abspath(path)) in self.opened

    def lang(self, value: str = "") -> str:
        value = value or settings_store.load().get("language", "auto")
        if value in ("ru", "en"):
            return value
        return "en" if memuai_preferences().get("language") == "en" else "ru"

    def theme(self) -> Dict[str, Any]:
        prefs = memuai_preferences()
        mode = settings_store.load().get("theme", "auto")
        if mode not in ("dark", "light"):
            mode = "light" if prefs.get("theme") == "light" else "dark"
        tokens: Dict[str, str] = {}
        try:
            from .. import themes                     # MemuAI palettes: same colours as the main app
            tokens = themes.resolve_tokens(mode, str(prefs.get(f"surface_{mode}") or ""),
                                           str(prefs.get("accent_color") or ""))
        except Exception as exc:  # noqa: BLE001 - standalone/dev: the page has its own defaults
            log.debug("theme tokens unavailable: %s", exc)
        safe = {k: v for k, v in tokens.items()
                if re.fullmatch(r"--[\w-]{1,40}", k) and isinstance(v, str) and len(v) < 200 and "<" not in v
                and ";" not in v and "}" not in v}
        return {"mode": mode, "tokens": safe, "design": str(prefs.get("ui_design") or ""),
                "font_scale": prefs.get("font_scale", 100)}

    # ---------------------------------------------------------------- documents
    def open_document(self, raw_path: str, encoding: str = "") -> Dict[str, Any]:
        path = _clean_path(raw_path)
        info = _stat(path)
        if not info["exists"]:
            raise FileNotFoundError(path)
        if os.path.isdir(path):
            raise IsADirectoryError(path)
        kind = kind_of(path)
        result: Dict[str, Any] = {"path": path, "name": os.path.basename(path), "kind": kind, **info}
        self.allow(path)
        if kind in RICH_KINDS:
            data = Path(path).read_bytes()
            from .formats import richhtml
            try:
                if kind == "docx":
                    from .formats.docx import read_docx
                    blocks, meta = read_docx(data)
                elif kind == "odt":
                    from .formats.odt import read_odt
                    blocks, meta = read_odt(data)
                else:
                    from .formats.rtf import read_rtf
                    blocks, meta = read_rtf(data)
            except Exception as exc:  # noqa: BLE001 - a damaged document: say so, offer plain text
                raise ValueError(f"cannot read {kind.upper()}: {exc}") from exc
            result["html"] = richhtml.to_html(blocks)
            result["meta"] = {k: v for k, v in (meta or {}).items() if k in ("properties", "images")}
            return result
        if kind == "sheet":
            from .formats.sheets import read_sheet
            try:
                result.update(read_sheet(path))
            except Exception as exc:  # noqa: BLE001 - damaged / not really a spreadsheet
                raise ValueError(f"cannot read the spreadsheet: {exc}") from exc
            return result
        if kind == "book":
            from .formats.books import read_book
            try:
                result.update(read_book(path))
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"cannot read the book: {exc}") from exc
            return result
        if kind in ("pdf", "image", "legacy", "slides"):
            return result
        from .formats import textio
        detected = textio.detect_file(path)
        if encoding:
            detected.encoding = textio.normalize_encoding(encoding)
            detected.bom = detected.encoding == "utf-8-sig"
        result.update({"encoding": detected.encoding, "bom": detected.bom, "eol": detected.eol,
                       "binary": detected.binary, "confidence": detected.confidence,
                       "web_encoding": detected.web_encoding})
        if detected.damaged:
            result["damaged"] = detected.damaged
        if detected.eol == "mixed":       # Round 34: saved with the most common ending, not the default one
            with open(path, "rb") as handle:
                sample = handle.read(textio.SAMPLE)
            wide = detected.encoding.startswith(("utf-16", "utf-32"))   # every other encoding keeps \r \n as bytes
            result["eol_major"] = textio.majority_eol(sample.decode(detected.encoding, errors="ignore") if wide else sample)
        if detected.binary and kind == "text":
            result["kind"] = "binary"
        if not detected.web_encoding:
            text, _ = textio.read_text(path, detected.encoding)
            result["text"] = text
        return result

    def save_text(self, path: str, text: str, encoding: str, eol: str, bom: Optional[bool], force: bool,
                  expected_mtime: int, opts: Dict[str, Any]) -> Dict[str, Any]:
        from .formats import textio
        path = _clean_path(path)
        current = _stat(path)
        if (not force and expected_mtime and current["exists"]
                and abs(current["mtime"] - expected_mtime) > 2):
            return {"ok": False, "conflict": True, **current}
        data, lost = None, 0
        if isinstance(text, (bytes, bytearray)):
            if not opts.get("trim") and not opts.get("final_newline"):
                data = textio.encode_utf8_bytes(bytes(text), encoding, eol, bom)   # Round 31: no str round trip
            if data is None:
                text = bytes(text).decode("utf-8", errors="surrogatepass")
        if data is None:
            if opts.get("trim"):
                text = re.sub(r"[ \t]+(?=\r?\n|$)", "", text)
            if opts.get("final_newline") and text and not text.endswith(("\n", "\r")):
                text += "\n"
            data, lost = textio.encode(text, encoding, eol, bom)
        if (current["exists"] and textio.normalize_encoding(encoding) in ("utf-8", "utf-8-sig") and textio._REPLACED in data
                and current.get("size", 0) <= 256 * 1048576):
            # Round 34: damaged UTF-8 — the broken bytes go back as they were (were: written as U+FFFD)
            data = textio.restore_damaged_utf8(data, Path(path).read_bytes())
        textio.atomic_write(path, data, keep_backup=bool(opts.get("backup")))
        self.allow(path)
        return {"ok": True, "lost": lost, **_stat(path)}

    def save_rich(self, path: str, html: str, fmt: str, force: bool, expected_mtime: int,
                  title: str = "", text: str = "") -> Dict[str, Any]:
        from .formats import richhtml, textio
        path = _clean_path(path)
        current = _stat(path)
        if (not force and expected_mtime and current["exists"]
                and abs(current["mtime"] - expected_mtime) > 2):
            return {"ok": False, "conflict": True, **current}
        fmt = fmt if fmt in RICH_WRITERS else "docx"
        blocks = richhtml.parse_html(html) if html else richhtml.text_to_blocks(text)
        title = title or os.path.splitext(os.path.basename(path))[0]
        if fmt == "docx":
            from .formats.docx import write_docx
            original = Path(path).read_bytes() if current["exists"] and kind_of(path) == "docx" else None
            data = write_docx(blocks, title, original)
        elif fmt == "odt":
            from .formats.odt import write_odt
            data = write_odt(blocks, title)
        elif fmt == "rtf":
            from .formats.rtf import write_rtf
            data = write_rtf(blocks)
        elif fmt == "html":
            data = standalone_html(richhtml.to_html(blocks) if not html else html, title).encode("utf-8")
        elif fmt == "md":
            data = (text if text and not html else richhtml.to_markdown(blocks)).encode("utf-8")
        else:
            data = (text if text and not html else richhtml.to_text(blocks)).encode("utf-8")
        textio.atomic_write(path, data, keep_backup=False)
        self.allow(path)
        return {"ok": True, **_stat(path)}


_HTML_CSS = """
body{font:16px/1.65 "Segoe UI",system-ui,-apple-system,Arial,sans-serif;max-width:820px;margin:40px auto;padding:0 24px;color:#1d1d1f;background:#fff}
h1,h2,h3,h4{line-height:1.25;margin:1.4em 0 .5em}h1{font-size:2em}h2{font-size:1.5em;border-bottom:1px solid #e5e5e5;padding-bottom:.25em}
a{color:#2360bc}code{font-family:"JetBrains Mono",Consolas,monospace;background:#f3f3f5;border-radius:4px;padding:.1em .35em;font-size:.9em}
pre{background:#f6f6f8;border:1px solid #e6e6ea;border-radius:8px;padding:12px 14px;overflow:auto}pre code{background:none;padding:0}
blockquote{margin:1em 0;padding:.2em 1em;border-left:4px solid #d0d0d6;color:#555}
table{border-collapse:collapse;margin:1em 0;width:auto}th,td{border:1px solid #d6d6dc;padding:6px 10px;vertical-align:top}th{background:#f3f3f6;text-align:left}
img{max-width:100%;height:auto}hr{border:0;border-top:1px solid #ddd;margin:2em 0}hr.mn-pagebreak{break-after:page;border-top:1px dashed #ccc}
ul.mn-tasks,ul.contains-task-list{list-style:none;padding-left:1.2em}li[data-checked=true]::before{content:"\\2611  "}li[data-checked=false]::before{content:"\\2610  "}
@media print{body{margin:0;max-width:none}}
"""


def standalone_html(body: str, title: str) -> str:
    import html as _html
    return ("<!doctype html>\n<html><head><meta charset=\"utf-8\">"
            f"<meta name=\"generator\" content=\"{APP_NAME}\"><title>{_html.escape(title)}</title>"
            f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><style>{_HTML_CSS}</style>"
            f"</head><body>\n{body}\n</body></html>\n")


# ===================================================================================== HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "MemuNote"
    sys_version = ""
    app: NoteApp = None    # type: ignore[assignment]

    def log_message(self, fmt, *args):  # noqa: D401 - quiet
        log.debug("%s - " + fmt, self.address_string(), *args)

    # ---------------------------------------------------------------- plumbing
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{self.app.port}", f"localhost:{self.app.port}")

    def _query(self) -> Dict[str, str]:
        return {k: v[-1] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}

    def _token_ok(self, allow_query: bool = False) -> bool:
        token = self.headers.get("X-MN-Token", "")
        if not token and allow_query:
            token = self._query().get("t", "")
        return bool(token) and secrets.compare_digest(token, self.app.token)

    def _send(self, status: int, body: bytes, ctype: str, extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, value: Any, status: int = 200) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, message: str, status: int = 400, **extra: Any) -> None:
        self._json({"ok": False, "error": message, **extra}, status)

    def _body(self, limit: int = MAX_JSON) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > limit:
            raise ValueError("request too large")
        return self.rfile.read(length) if length else b""

    def _json_body(self) -> Dict[str, Any]:
        raw = self._body()
        value = json.loads(raw or b"{}")
        if not isinstance(value, dict):
            raise ValueError("JSON object expected")
        return value

    # ---------------------------------------------------------------- verbs
    def do_GET(self):  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self):  # noqa: N802
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self):  # noqa: N802
        self._dispatch("POST")

    def do_DELETE(self):  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        if not self._host_ok():
            self._error("forbidden host", 403)
            return
        route = urllib.parse.urlsplit(self.path).path
        try:
            if not route.startswith("/api/"):
                if method != "GET":
                    self._error("method not allowed", 405)
                    return
                self._static(route)
                return
            name = route[5:].replace("/", "_")
            handler = getattr(self, f"api_{name}", None)
            if handler is None:
                self._error("not found", 404)
                return
            needs_query_token = name in ("raw", "asset")
            if not self._token_ok(allow_query=needs_query_token):
                self._error("unauthorized", 401)
                return
            handler(method)
        except FileNotFoundError as exc:
            self._error("not_found", 404, path=str(exc))
        except IsADirectoryError:
            self._error("is_directory", 400)
        except PermissionError as exc:
            self._error("permission_denied", 403, detail=str(exc))
        except (ValueError, json.JSONDecodeError) as exc:
            self._error(str(exc)[:500], 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001
            log.exception("request %s failed", route)
            try:
                self._error(f"{type(exc).__name__}: {exc}"[:500], 500)
            except OSError:
                pass

    def _static(self, route: str) -> None:
        if route in ("", "/"):
            route = "/index.html"
        relative = urllib.parse.unquote(route).lstrip("/")
        target = (STATIC / relative).resolve()
        try:
            target.relative_to(STATIC)
        except ValueError:
            self._error("not found", 404)
            return
        if not target.is_file():
            self._error("not found", 404)
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix in (".js", ".mjs"):
            ctype = "text/javascript"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        data = target.read_bytes()
        cache = "no-cache" if target.suffix == ".html" else "max-age=86400"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        if target.suffix == ".html":
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                             "script-src 'self'; worker-src 'self' blob:; connect-src 'self'; font-src 'self' data:; "
                             "frame-src 'self' blob:; object-src 'none'; base-uri 'none'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    # ================================================================ API: app
    def api_boot(self, method: str) -> None:
        app = self.app
        settings = settings_store.load()
        session = read_json(note_dir() / "session.json", {}) if settings.get("restore_session") else {}
        recent = read_json(note_dir() / "recent.json", [])
        self._json({
            "ok": True, "app": APP_NAME, "service": SERVICE, "version": _version(), "lang": app.lang(),
            "theme": app.theme(), "settings": settings, "session": session,
            "recent": [r for r in recent if isinstance(r, str)][:30],
            "files": app.take_initial_files(), "events_seq": app.events.delivered, "platform": os.name, "desktop": app.desktop is not None,
            "home": os.path.expanduser("~"), "sep": os.sep,
            "fonts": settings_store.FONT_PRESETS,
        })

    def api_settings(self, method: str) -> None:
        if method == "GET":
            self._json({"ok": True, "settings": settings_store.load()})
            return
        value = settings_store.save(self._json_body())
        self._json({"ok": True, "settings": value, "lang": self.app.lang(), "theme": self.app.theme()})

    def api_theme(self, method: str) -> None:
        self._json({"ok": True, "theme": self.app.theme(), "lang": self.app.lang()})

    def api_session(self, method: str) -> None:
        body = self._json_body()
        tabs = body.get("tabs") if isinstance(body.get("tabs"), list) else []
        clean = {"tabs": [t for t in tabs if isinstance(t, dict)][:300], "active": str(body.get("active") or "")[:80],
                 "layout": body.get("layout") if isinstance(body.get("layout"), dict) else {},
                 "saved_at": int(time.time())}
        write_json(note_dir() / "session.json", clean)
        self._json({"ok": True})

    def api_recent(self, method: str) -> None:
        path_file = note_dir() / "recent.json"
        recent = [r for r in read_json(path_file, []) if isinstance(r, str)]
        if method == "POST":
            body = self._json_body()
            if body.get("clear"):
                recent = []
            elif body.get("remove"):
                recent = [r for r in recent if r != body["remove"]]
            elif body.get("path"):
                item = _clean_path(body["path"])
                recent = [item] + [r for r in recent if os.path.normcase(r) != os.path.normcase(item)]
            write_json(path_file, recent[:30])
        self._json({"ok": True, "recent": recent[:30]})

    def api_events(self, method: str) -> None:
        try:
            since = int(self._query().get("since") or 0)
        except ValueError:
            since = 0
        self.app.last_seen = time.monotonic()
        seq, items = self.app.events.wait(since, 25.0)
        self.app.last_seen = time.monotonic()
        self._json({"ok": True, "seq": seq, "events": items})

    # ================================================================ API: files
    def api_open(self, method: str) -> None:
        body = self._json_body()
        result = self.app.open_document(body.get("path", ""), str(body.get("encoding") or ""))
        self._json({"ok": True, **result})

    def api_raw(self, method: str) -> None:
        path = _clean_path(self._query().get("path", ""))
        if not self.app.allowed(path):
            self._error("not opened", 403)
            return
        total = os.path.getsize(path)
        try:   # Round 31: ?offset=N — only the bytes appended since N (log tail / follow mode)
            offset = max(0, min(total, int(self._query().get("offset") or 0)))
        except ValueError:
            offset = 0
        size = total - offset
        ctype = "application/pdf" if path.lower().endswith(".pdf") else "application/octet-stream"
        if kind_of(path) == "image":
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-MN-Size", str(total))
        if ctype == "image/svg+xml":       # an SVG opened directly must not run scripts
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        self.end_headers()
        if self.command == "HEAD":
            return
        # Exactly Content-Length bytes even if the file grows meanwhile (a growing log broke the response).
        with open(path, "rb") as handle:
            handle.seek(offset)
            left = size
            while left > 0:
                chunk = handle.read(min(left, 1024 * 1024))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def api_asset(self, method: str) -> None:
        """Pictures referenced by a Markdown preview (local images only)."""
        path = _clean_path(self._query().get("path", ""))
        if os.path.splitext(path)[1].lower() not in IMAGE_EXT or not os.path.isfile(path):
            self._error("not an image", 404)
            return
        data = Path(path).read_bytes()
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        extra = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"} if path.endswith(".svg") else {}
        self._send(200, data, ctype, extra)

    def api_save_text(self, method: str) -> None:
        query = self._query()
        raw = self._body(MAX_TEXT)
        bom = query.get("bom")
        result = self.app.save_text(query.get("path", ""), raw, query.get("encoding", "utf-8"),
                                    query.get("eol", "lf"), None if bom in (None, "") else bom == "1",
                                    query.get("force") == "1", int(query.get("mtime") or 0),
                                    {"trim": query.get("trim") == "1", "final_newline": query.get("final_newline") == "1",
                                     "backup": query.get("backup") == "1"})
        self._json(result, 200 if result.get("ok") or result.get("conflict") else 500)

    def api_save_binary(self, method: str) -> None:
        """Round 31: bytes built by the page (an edited PDF from pdf-lib) — atomic write + conflict check."""
        query = self._query()
        path = _clean_path(query.get("path", ""))
        raw = self._body(MAX_TEXT)
        if kind_of(path) == "pdf" and not raw.lstrip()[:5] == b"%PDF-":
            self._error("not a PDF", 400)
            return
        if kind_of(path) == "slides" and raw[:4] != b"PK\x03\x04":      # Round 32: a presentation is a ZIP package
            self._error("not a presentation", 400)
            return
        current = _stat(path)
        expected = int(query.get("mtime") or 0)
        if (query.get("force") != "1" and expected and current["exists"] and abs(current["mtime"] - expected) > 2):
            self._json({"ok": False, "conflict": True, **current})
            return
        from .formats.textio import atomic_write
        atomic_write(path, raw, keep_backup=query.get("backup") == "1")
        self.app.allow(path)
        self._json({"ok": True, **_stat(path)})

    def api_font(self, method: str) -> None:
        """Round 31: a Unicode TrueType font of the system for text in edited PDFs (Cyrillic needs one)."""
        from .formats.pdftools import unicode_font_path
        path = unicode_font_path(self._query().get("style", ""))
        if not path:
            self._error("no_font", 404)
            return
        self._send(200, Path(path).read_bytes(), "font/ttf", {"Cache-Control": "max-age=86400",
                                                             "X-MN-Font": os.path.basename(path)})

    def api_pdf_text(self, method: str) -> None:
        """Round 31: text / Markdown of a PDF on disk (pypdf), for PDFs too big for the page or without a view."""
        from .formats import pdftools
        body = self._json_body()
        path = _clean_path(body.get("path", ""))
        if not self.app.allowed(path):
            self._error("not opened", 403)
            return
        fmt = "markdown" if body.get("format") == "markdown" else "text"
        self._json({"ok": True, "text": pdftools.extract(path, fmt, int(body.get("max_chars") or 2_000_000))})

    def api_save_rich(self, method: str) -> None:
        body = self._json_body()
        result = self.app.save_rich(body.get("path", ""), str(body.get("html") or ""), str(body.get("format") or ""),
                                    bool(body.get("force")), int(body.get("mtime") or 0), str(body.get("title") or ""),
                                    str(body.get("text") or ""))
        self._json(result)

    def api_export(self, method: str) -> None:
        body = self._json_body()
        fmt = str(body.get("format") or "")
        if fmt == "pdf":
            path = _clean_path(body.get("path", ""))
            desktop = self.app.desktop
            if desktop is None or not hasattr(desktop, "print_to_pdf"):
                self._error("print_unavailable", 501)
                return
            ok, detail = desktop.print_to_pdf(path)
            if not ok:
                self._error(detail or "print_failed", 500)
                return
            self._json({"ok": True, **_stat(path)})
            return
        result = self.app.save_rich(body.get("path", ""), str(body.get("html") or ""), fmt, True, 0,
                                    str(body.get("title") or ""), str(body.get("text") or ""))
        self._json(result)

    def api_convert(self, method: str) -> None:
        """HTML ↔ Markdown / text conversions (Document → Markdown, rich paste cleanup)."""
        from .formats import richhtml
        body = self._json_body()
        blocks = richhtml.parse_html(str(body.get("html") or ""))
        target = body.get("to")
        if target == "markdown":
            self._json({"ok": True, "text": richhtml.to_markdown(blocks)})
        elif target == "text":
            self._json({"ok": True, "text": richhtml.to_text(blocks)})
        else:
            self._json({"ok": True, "html": richhtml.to_html(blocks)})

    def api_stat(self, method: str) -> None:
        body = self._json_body()
        paths = [p for p in body.get("paths") or [] if isinstance(p, str)][:500]
        out = {}
        for item in paths:
            try:
                out[item] = _stat(_clean_path(item))
            except ValueError:
                out[item] = {"exists": False}
        self._json({"ok": True, "files": out})

    def api_rename(self, method: str) -> None:
        body = self._json_body()
        source, target = _clean_path(body.get("from")), _clean_path(body.get("to"))
        if os.path.exists(target) and not body.get("overwrite"):
            self._error("exists", 409)
            return
        os.replace(source, target)
        self.app.allow(target)
        self._json({"ok": True, **_stat(target)})

    def api_fs_list(self, method: str) -> None:
        body = self._json_body()
        folder = _clean_path(body.get("dir") or os.path.expanduser("~"))
        entries = []
        with os.scandir(folder) as items:
            for entry in items:
                if entry.name.startswith((".", "$")) and not body.get("hidden"):
                    continue
                try:
                    is_dir = entry.is_dir()
                    st = entry.stat()
                except OSError:
                    continue
                entries.append({"name": entry.name, "path": entry.path, "dir": is_dir,
                                "size": 0 if is_dir else st.st_size, "mtime": st.st_mtime_ns // 1_000_000,
                                "kind": "" if is_dir else kind_of(entry.name)})
                if len(entries) >= 5000:
                    break
        entries.sort(key=lambda e: (not e["dir"], e["name"].casefold()))
        parent = os.path.dirname(folder)
        self._json({"ok": True, "dir": folder, "parent": parent if parent != folder else "", "entries": entries})

    def api_recovery(self, method: str) -> None:
        query = self._query()
        ident = query.get("id", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ident or ""):
            if method == "GET" and not ident:
                rows = []
                for meta in sorted(recovery_dir().glob("*.json")):
                    data = read_json(meta, {})
                    if data:
                        rows.append({"id": meta.stem, **{k: data.get(k) for k in ("title", "path", "saved_at", "size")}})
                self._json({"ok": True, "items": rows})
                return
            self._error("bad id")
            return
        text_file, meta_file = recovery_dir() / f"{ident}.txt", recovery_dir() / f"{ident}.json"
        if method == "DELETE":
            for item in (text_file, meta_file):
                try:
                    item.unlink()
                except OSError:
                    pass
            self._json({"ok": True})
            return
        if method == "GET":
            if not text_file.is_file():
                self._error("not_found", 404)
                return
            meta = read_json(meta_file, {})
            self._json({"ok": True, "text": text_file.read_text(encoding="utf-8", errors="replace"), "meta": meta})
            return
        raw = self._body(MAX_TEXT)
        from .formats.textio import atomic_write
        atomic_write(text_file, raw)
        meta = {"title": query.get("title", "")[:200], "path": query.get("path", "")[:4096],
                "kind": query.get("kind", "")[:20], "saved_at": int(time.time()), "size": len(raw)}
        write_json(meta_file, meta)
        self._json({"ok": True})

    # ================================================================ API: desktop glue
    def api_dialog(self, method: str) -> None:
        body = self._json_body()
        desktop = self.app.desktop
        if desktop is None:
            self._error("no_desktop", 501)
            return
        paths = desktop.file_dialog(str(body.get("mode") or "open"), str(body.get("directory") or ""),
                                    str(body.get("filename") or ""), body.get("filters") or [],
                                    bool(body.get("multiple", True)))
        self._json({"ok": True, "paths": paths or []})

    def api_window(self, method: str) -> None:
        body = self._json_body()
        desktop = self.app.desktop
        if desktop is not None:
            desktop.window_command(str(body.get("cmd") or ""), body)
        self._json({"ok": True})

    def api_reveal(self, method: str) -> None:
        body = self._json_body()
        path = _clean_path(body.get("path"))
        from . import winsys
        winsys.reveal(path)
        self._json({"ok": True})

    def api_external(self, method: str) -> None:
        body = self._json_body()
        url = str(body.get("url") or "")
        if not re.match(r"^(https?://|mailto:)", url, re.I):
            self._error("only http(s) and mailto links")
            return
        import webbrowser
        webbrowser.open(url)
        self._json({"ok": True})

    def api_openwith(self, method: str) -> None:
        from . import winsys
        if method == "GET":
            self._json({"ok": True, **winsys.open_with_state()})
            return
        body = self._json_body()
        result = winsys.register_open_with(bool(body.get("enable")))
        self._json({"ok": True, **result})

    # ================================================================ API: AI
    def api_ai_status(self, method: str) -> None:
        from . import ai
        settings = settings_store.load()
        value = ai.status(settings.get("proxy_url", ""), force=self._query().get("force") == "1")
        self._json({"ok": True, **value})

    def api_ai_search(self, method: str) -> None:
        from . import ai
        body = self._json_body()
        query = str(body.get("query") or "").strip()
        if not query:
            self._error("empty query")
            return
        try:
            rows = ai.websearch(ai.discover(settings_store.load().get("proxy_url", "")), query,
                                int(body.get("limit") or 8))
        except ai.AIError as exc:
            self._error(str(exc), 502, kind=exc.kind)
            return
        self._json({"ok": True, "results": rows})

    def api_bigdocs(self, method: str) -> None:
        """Round 35: documents of any size for the AI panel (app/memunote/bigdocs_api.py)."""
        from . import bigdocs_api
        self._json(bigdocs_api.handle(self._json_body(), settings_store.load(), self.app.lang()))

    def api_ai_stop(self, method: str) -> None:
        body = self._json_body()
        stream = self.app.streams.get(str(body.get("id") or ""))
        if stream:
            stream["cancel"].set()
            conn = stream.get("conn")
            try:
                if conn is not None and conn.sock is not None:
                    conn.sock.shutdown(2)
            except OSError:
                pass
        self._json({"ok": True})

    def api_ai_chat(self, method: str) -> None:
        from . import ai, prompts
        body = self._json_body()
        settings = settings_store.load()
        if not settings.get("ai_enabled", True):
            self._error("ai_disabled", 403)
            return
        model = str(body.get("model") or settings.get("ai_model") or "")
        base = ai.discover(settings.get("proxy_url", ""))
        if not model:
            status = ai.status(settings.get("proxy_url", ""))
            model = status.get("default_model") or ""
        if not model:
            self._error("no_model", 503)
            return
        use_memory = bool(body.get("memory", settings.get("ai_memory", True)))
        built = prompts.build_messages(body, settings, self.app.lang(), use_memory)
        request_id = str(body.get("id") or uuid.uuid4().hex)[:64]
        cancel = threading.Event()
        stream_state: Dict[str, Any] = {"cancel": cancel, "conn": None}
        self.app.streams[request_id] = stream_state
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def emit(event: Dict[str, Any]) -> bool:
            try:
                self.wfile.write(b"data: " + json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n\n")
                self.wfile.flush()
                return True
            except OSError:
                cancel.set()
                return False

        raw_parts: List[str] = []
        flt = ai.memory_filter() if built["memory_write"] else None
        edit_stream = None
        if body.get("action") == "agent":            # Round 31: edit blocks → "edit*" events (app/memunote/edits.py)
            from .edits import EditStream
            edit_stream = EditStream()

        def emit_visible(text: str) -> bool:
            if edit_stream is None:
                return emit({"t": "delta", "text": text})
            for kind, value in edit_stream.feed(text):
                if not emit({"t": "delta", "text": value} if kind == "delta" else {"t": kind, **value}):
                    return False
            return True

        emit({"t": "start", "id": request_id, "model": model, "memory_facts": built["memory_facts"],
              "agent": edit_stream is not None})
        try:
            payload = {"model": model, "messages": built["messages"],
                       "web_search": bool(body.get("web_search")), "thinking": bool(body.get("thinking")),
                       "max_tokens": int(body.get("max_tokens") or 0) or None,
                       "temperature": body.get("temperature"), "effort": str(body.get("effort") or "")[:16]}   # r33: effort
            for event in ai.stream_chat(base, payload, cancel, lambda c: stream_state.__setitem__("conn", c)):
                if event["t"] == "delta":
                    raw_parts.append(event["text"])
                    visible = flt.feed(event["text"]) if flt is not None else event["text"]
                    if visible and not emit_visible(visible):
                        break
                    continue
                if not emit(event):
                    break
            if flt is not None and not cancel.is_set():
                tail = flt.finish()
                if tail:
                    emit_visible(tail)
            if edit_stream is not None:
                # also after Stop: a half-written edit is offered as "incomplete" (the user decides)
                for kind, value in edit_stream.finish():
                    emit({"t": "delta", "text": value} if kind == "delta" else {"t": kind, **value})
            if built["memory_write"] and raw_parts and not cancel.is_set():
                stored = ai.memory_store("".join(raw_parts), built["excerpt"], built["title"])
                if any(stored.values()):
                    emit({"t": "memory", **stored})
            emit({"t": "done", "cancelled": cancel.is_set()})
        except ai.AIError as exc:
            emit({"t": "error", "message": str(exc), "kind": exc.kind, "status": exc.status})
        except Exception as exc:  # noqa: BLE001
            if not cancel.is_set():
                log.exception("ai chat failed")
                emit({"t": "error", "message": f"{type(exc).__name__}: {exc}"[:400], "kind": "internal"})
        finally:
            self.app.streams.pop(request_id, None)
            self.close_connection = True

    # ================================================================ remote control (second instance)
    def api_remote(self, method: str) -> None:
        body = self._json_body()
        action = body.get("action")
        if action == "open":
            files = [str(p) for p in body.get("paths") or [] if isinstance(p, str)][:200]
            self.app.events.push({"type": "open", "paths": files})
            if self.app.desktop is not None:
                self.app.desktop.window_command("activate", {})
        elif action == "shutdown":
            self.app.events.push({"type": "shutdown"})
            if self.app.desktop is not None and self.app.ui == "window":
                threading.Timer(4.0, self.app.desktop.force_close).start()
            elif self.app.desktop is not None:          # a ghost: the window never appeared
                threading.Timer(0.5, self.app.desktop.force_close).start()
            else:                                       # browser / headless: the main loop returns
                threading.Timer(1.0, self.app.stopping.set).start()
        elif action == "activate" and self.app.desktop is not None:
            self.app.desktop.window_command("activate", {})
        elif action == "settings":                      # Round 38: changed in MemuAI → the page re-reads and applies
            self.app.events.push({"type": "settings"})
        self._json({"ok": True, "pid": os.getpid(), "ui": self.app.ui,
                    "age": round(time.time() - self.app.started, 1)})


def _version() -> str:
    try:
        from ..version import APP_VERSION
        return str(APP_VERSION)
    except Exception:  # noqa: BLE001
        return ""


try:   # Round 32 (photo): the photo editor's routes /api/photo/* (app/memunote/photo.py)
    from . import photo as _photo
    _photo.register(Handler)
except Exception:  # noqa: BLE001 - the notepad works without the photo editor backend
    log.exception("photo routes unavailable")


try:   # Round 32 (slides): AI for presentations /api/slides_ai (app/memunote/slides_api.py)
    from . import slides_api as _slides_api
    _slides_api.register(Handler)
except Exception:  # noqa: BLE001 - the notepad works without it
    log.exception("slides routes unavailable")


try:   # Round 40 (r40-note): save edited .xlsx cells /api/sheet_save (app/memunote/sheets_api.py)
    from . import sheets_api as _sheets_api
    _sheets_api.register(Handler)
except Exception:  # noqa: BLE001 - the notepad works without it (spreadsheets stay read-only)
    log.exception("sheet routes unavailable")


try:   # Round 33: run the open file /api/run/* (app/memunote/runner.py)
    from . import runner as _runner
    _runner.register(Handler)
except Exception:  # noqa: BLE001 - the notepad works without it
    log.exception("run routes unavailable")


try:   # Round 33: spell checking /api/spell/* (app/memunote/spell.py) and code completion data
    from . import spell as _spell
    _spell.register(Handler)
except Exception:  # noqa: BLE001 - the notepad works without it
    log.exception("spell routes unavailable")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def start(app: NoteApp, port: int = 0) -> Tuple[Server, threading.Thread]:
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = Server(("127.0.0.1", port), handler)
    app.port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, name="memunote-http", daemon=True)
    thread.start()
    return server, thread
