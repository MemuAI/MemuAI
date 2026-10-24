"""The local HTTP backend of MemuFiles (stdlib ``ThreadingHTTPServer``, like MemuNote — no FastAPI).

Why stdlib and not FastAPI: MemuFiles must open in well under a second; the
frozen app excludes fastapi/uvicorn/pydantic (see build.py TARGETS["memufiles"]).

Security (same model as MemuNote): binds ``127.0.0.1`` on a random port; every
``/api`` call needs the per-process token (``X-MF-Token`` header, or ``t=`` for
``<img>``/``<video>`` GETs); the ``Host`` header must be the loopback address
(DNS-rebinding guard); static files only from ``static/``.

Big listings stream as NDJSON (``/api/ls``, ``/api/search``): the page renders
the first rows while the rest are still being read.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import secrets
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import APP_NAME, SERVICE
from . import fsops
from . import settings as settings_store
from .fsops import FsError, clean_path
from .paths import files_dir, memuai_preferences, read_json, write_json

log = logging.getLogger("memufiles.server")

STATIC = Path(__file__).resolve().parent / "static"
MAX_JSON = 32 * 1024 * 1024


class Events:
    """Long-poll event queue: operation progress, folder changes, second-instance paths, drops."""

    def __init__(self) -> None:
        self.items: List[Tuple[int, Dict[str, Any]]] = []
        self.seq = 0
        self.cond = threading.Condition()

    def push(self, event: Dict[str, Any]) -> None:
        with self.cond:
            self.seq += 1
            # collapse progress of the same operation: only the newest snapshot matters
            if event.get("type") == "op":
                op_id = event.get("op", {}).get("id")
                self.items = [(s, e) for s, e in self.items
                              if not (e.get("type") == "op" and e.get("op", {}).get("id") == op_id)]
            self.items.append((self.seq, event))
            self.items = self.items[-300:]
            self.cond.notify_all()

    def wait(self, since: int, timeout: float = 25.0) -> Tuple[int, List[Dict[str, Any]]]:
        deadline = time.monotonic() + timeout
        with self.cond:
            if since > self.seq:          # the server restarted: start over
                since = 0
            while True:
                fresh = [e for s, e in self.items if s > since]
                if fresh or time.monotonic() >= deadline:
                    return self.seq, fresh
                self.cond.wait(max(0.05, deadline - time.monotonic()))


class FilesApp:
    """State shared by the request handlers; ``desktop`` is the window glue (None in tests/dev)."""

    def __init__(self, paths: Optional[List[str]] = None, reveal: Optional[List[str]] = None,
                 shell: Optional[List[str]] = None, browse: Optional[List[str]] = None) -> None:
        self.token = secrets.token_urlsafe(24)
        self.events = Events()
        self.initial_paths = [p for p in (paths or []) if p]
        self.initial_reveal: List[str] = list(reveal or [])     # r41-files: «explorer /select,» — parent + selection
        self.initial_shell: List[str] = list(shell or [])       # r41-files: from the folder handler (browser «Show in folder»)
        self.initial_browse: List[str] = list(browse or [])     # r41-files: archives opened like folders (association)
        self.ui_state: Dict[str, Any] = {}                       # r41-files: current folder + selection (Quick Access)
        self.desktop: Any = None
        self.port = 0
        self.started = time.time()
        self.ui = "starting"
        self.last_seen = 0.0
        self.stopping = threading.Event()
        self.booted = False            # Round 39: a page has taken the start-up paths (until then hand-offs queue up)
        self._lazy_lock = threading.Lock()
        self._queue: Any = None
        self._jobs: Any = None
        self._plans: Any = None
        self.streams: Dict[str, Dict[str, Any]] = {}
        self.searches: Dict[str, threading.Event] = {}

    # Round 39 (r39-files-perf): the operation queue, jobs and AI plans are built on first use (or by the deferred
    # warm-up after the first page boot) — their imports (zipfile, hashlib, the AI client…) stay off the start-up path.
    @property
    def queue(self) -> Any:
        if self._queue is None:
            with self._lazy_lock:
                if self._queue is None:
                    from .opqueue import OpQueue
                    self._queue = OpQueue(notify=self._op_event,
                                          changed=lambda folders: self.events.push({"type": "changed", "paths": folders}))
        return self._queue

    def _op_event(self, snap: Dict[str, Any]) -> None:
        self.events.push({"type": "op", "op": snap})
        taskbar = getattr(self.desktop, "taskbar", None)       # r40-files-core: progress on the taskbar button
        if taskbar is not None:
            taskbar.op_changed(snap)

    @property
    def jobs(self) -> Any:
        if self._jobs is None:
            with self._lazy_lock:
                if self._jobs is None:
                    from .jobs import Jobs
                    self._jobs = Jobs()
        return self._jobs

    @property
    def plans(self) -> Any:
        if self._plans is None:
            with self._lazy_lock:
                if self._plans is None:
                    from .assistant import PlanStore
                    self._plans = PlanStore()
        return self._plans

    def take_initial_paths(self) -> List[str]:
        paths, self.initial_paths = self.initial_paths, []
        self.booted = True
        return paths

    def take_initial_marks(self) -> Tuple[List[str], List[str], List[str]]:
        marks = (self.initial_reveal, self.initial_shell, self.initial_browse)
        self.initial_reveal, self.initial_shell, self.initial_browse = [], [], []
        return marks

    def lang(self) -> str:
        value = settings_store.load().get("language", "auto")
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
            from .. import themes                    # MemuAI palettes: same colours as the main app
            tokens = themes.resolve_tokens(mode, str(prefs.get(f"surface_{mode}") or ""),
                                           str(prefs.get("accent_color") or ""))
        except Exception as exc:  # noqa: BLE001
            log.debug("theme tokens unavailable: %s", exc)
        safe = {k: v for k, v in tokens.items()
                if re.fullmatch(r"--[\w-]{1,40}", k) and isinstance(v, str) and len(v) < 200 and "<" not in v
                and ";" not in v and "}" not in v}
        return {"mode": mode, "tokens": safe, "font_scale": prefs.get("font_scale", 100)}


def _version() -> str:
    try:
        from ..version import APP_VERSION
        return str(APP_VERSION)
    except Exception:  # noqa: BLE001
        return ""


_STATIC_CACHE: Dict[str, Tuple[int, int, bytes, str]] = {}


def _static_bytes(target: Path) -> Tuple[bytes, str]:
    """Round 39: static files from memory, re-read only when size/mtime change; a weak ETag from both."""
    st = target.stat()
    key = str(target)
    hit = _STATIC_CACHE.get(key)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2], hit[3]
    data = target.read_bytes()
    etag = f'W/"{st.st_mtime_ns:x}-{st.st_size:x}"'
    _STATIC_CACHE[key] = (st.st_mtime_ns, st.st_size, data, etag)
    return data, etag


def _themed_html(data: bytes, app: "FilesApp") -> bytes:
    """The user's theme mode, palette (``theme()`` tokens are already sanitised) and language in the HTML itself,
    so the skeleton is painted in the final colours by the very first frame; app.js drops ``#mf-theme`` once it
    applies the tokens itself."""
    try:
        theme = app.theme()
        mode, lang, tokens = theme["mode"], app.lang(), theme.get("tokens") or {}
    except Exception:  # noqa: BLE001
        return data
    css = "".join(f"{k}:{v};" for k, v in tokens.items() if '"' not in v and "\\" not in v)
    fallback = "" if tokens else ' class="fallback-tokens"'
    head = f'<html lang="{lang}" data-theme="{mode}"{fallback}>'
    out = data.replace(b'<html lang="ru" data-theme="dark">', head.encode("utf-8"), 1)
    if css:
        out = out.replace(b"</head>", f'<style id="mf-theme">:root{{{css}}}</style>\n</head>'.encode("utf-8"), 1)
    return out


# ===================================================================================== HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "MemuFiles"
    sys_version = ""
    app: FilesApp = None    # type: ignore[assignment]

    def log_message(self, fmt, *args):  # noqa: D401
        log.debug("%s - " + fmt, self.address_string(), *args)

    # ---------------------------------------------------------------- plumbing
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{self.app.port}", f"localhost:{self.app.port}")

    def _query(self) -> Dict[str, str]:
        return {k: v[-1] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}

    def _token_ok(self, allow_query: bool = False) -> bool:
        token = self.headers.get("X-MF-Token", "")
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
        self._send(status, json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

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

    def _stream_start(self, ctype: str = "application/x-ndjson; charset=utf-8") -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.close_connection = True

    def _line(self, value: Any) -> bool:
        try:
            self.wfile.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
            self.wfile.flush()
            return True
        except OSError:
            return False

    # ---------------------------------------------------------------- verbs
    def do_GET(self):  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self):  # noqa: N802
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802
        self._dispatch("POST")

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
            if handler is None and name.startswith("x_"):        # Round 39 (r39-files-feat): extras.py features
                handler = lambda m, op=name[2:]: self._extras(op, m)   # noqa: E731
            if handler is None and name.startswith("x40_"):      # r40-files-max: feat40.py (Properties → Details)
                handler = lambda m, op=name[4:]: self._feat40(op, m)   # noqa: E731
            if handler is None:
                self._error("not found", 404)
                return
            if not self._token_ok(allow_query=name in ("raw", "thumb", "appicon")):
                self._error("unauthorized", 401)
                return
            handler(method)
        except FsError as exc:
            self._error(exc.code, 409 if exc.code in ("exists", "protected", "busy") else 400,
                        detail=str(exc)[:500], **{k: v for k, v in exc.extra.items() if isinstance(v, (str, int))})
        except FileNotFoundError as exc:
            self._error("not_found", 404, detail=str(exc)[:500])
        except FileExistsError as exc:
            self._error("exists", 409, detail=str(exc)[:500])
        except NotADirectoryError as exc:
            self._error("not_a_directory", 400, detail=str(exc)[:500])
        except IsADirectoryError as exc:
            self._error("is_directory", 400, detail=str(exc)[:500])
        except PermissionError as exc:
            self._error("permission_denied", 403, detail=str(exc)[:500])
        except (ValueError, json.JSONDecodeError) as exc:
            self._error(str(exc)[:500], 400)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except OSError as exc:
            self._error("os_error", 500, detail=(exc.strerror or str(exc))[:500])
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
        data, etag = _static_bytes(target)
        if target.suffix == ".html":         # Round 39: the right theme/language from the first frame (no dark↔light flash)
            data = _themed_html(data, self.app)
            etag = ""
        if etag and self.headers.get("If-None-Match") == etag:
            self.send_response(304)          # Round 39: WebView2 revalidates (no-cache) — unchanged files cost no body
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        if etag:
            self.send_header("ETag", etag)
        self.send_header("X-Content-Type-Options", "nosniff")
        if target.suffix == ".html":
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
                             "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
                             "font-src 'self' data:; frame-src 'self' blob:; object-src 'self'; base-uri 'none'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    # ================================================================ app
    def api_boot(self, method: str) -> None:
        app = self.app
        settings = _migrate_views(settings_store.load())
        session = read_json(files_dir() / "session.json", {}) if settings.get("restore_session") else {}
        seq = app.events.seq                 # Round 39: read before the paths, so a hand-off in between is not lost
        paths = app.take_initial_paths()
        reveal, shell, browse = app.take_initial_marks()
        _deferred_warm(app)
        self._json({"ok": True, "app": APP_NAME, "service": SERVICE, "version": _version(), "lang": app.lang(),
                    "theme": app.theme(), "settings": settings, "session": session,
                    "paths": paths, "path_info": path_info(paths), "known_folders": known_folders(),
                    "reveal": reveal, "shell": shell, "browse": browse,
                    "events_seq": seq, "platform": os.name,
                    "desktop": app.desktop is not None, "home": os.path.expanduser("~"), "sep": os.sep,
                    "dnd_paths": bool(getattr(app.desktop, "dnd_paths", False)),     # r40-files-core
                    "tags": list(settings_store.tags_all().values()), "tag_colors": settings_store.TAG_COLORS})

    def api_settings(self, method: str) -> None:
        if method == "GET":
            self._json({"ok": True, "settings": settings_store.load(), "lang": self.app.lang(), "theme": self.app.theme()})
            return
        body = self._json_body()
        value = settings_store.save(body)
        _warm_settings_changed(self.app)
        if "open_archives" in body:
            _sync_archive_assoc()                        # r41-files
        self._json({"ok": True, "settings": value, "lang": self.app.lang(), "theme": self.app.theme()})

    def api_settings_schema(self, method: str) -> None:   # r41-files: the settings window is built from the schema
        from . import settings_schema
        self._json({"ok": True, **settings_schema.public()})

    def api_uistate(self, method: str) -> None:           # r41-files: the page reports its folder + selection
        body = self._json_body() if method == "POST" else {}
        if method == "POST":
            sel = [str(p) for p in (body.get("selected") or []) if isinstance(p, str)][:200]
            self.app.ui_state = {"folder": str(body.get("folder") or "")[:4096], "selected": sel,
                                 "mode": str(body.get("mode") or "")[:20], "archive": bool(body.get("archive")),
                                 "at": time.time()}
        self._json({"ok": True, "state": _public_state(self.app)})

    def api_default_shell(self, method: str) -> None:
        """«MemuFiles по умолчанию» (Round 38): GET status; POST ``{action: enable|disable|restore|hook, feature?, on?}``."""
        from . import default_shell
        if method == "GET":
            self._json(default_shell.status())
            return
        body = self._json_body()
        result = default_shell.handle(body)
        if body.get("action") in ("hook", "restore") and os.name == "nt" and self.app.desktop is not None:
            from . import winkey                   # the Win+E fallback lives in this process: apply it now
            if result.get("hook"):
                home = os.path.expanduser("~")
                winkey.start(lambda: (self.app.events.push({"type": "open", "paths": [home]}),
                                      self.app.desktop.window_command("activate", {})))
            else:
                winkey.stop()
        self._json(result)

    def api_session(self, method: str) -> None:
        body = self._json_body()
        tabs = body.get("tabs") if isinstance(body.get("tabs"), list) else []
        clean = {"tabs": [t for t in tabs if isinstance(t, dict)][:100], "active": int(body.get("active") or 0),
                 "layout": body.get("layout") if isinstance(body.get("layout"), dict) else {},
                 "closed": [t for t in (body.get("closed") or []) if isinstance(t, dict)][:20],
                 "saved_at": int(time.time())}
        write_json(files_dir() / "session.json", clean)
        self._json({"ok": True})

    def api_events(self, method: str) -> None:
        try:
            since = int(self._query().get("since") or 0)
        except ValueError:
            since = 0
        self.app.last_seen = time.monotonic()
        seq, items = self.app.events.wait(since, 25.0)
        self.app.last_seen = time.monotonic()
        self._json({"ok": True, "seq": seq, "events": items})

    # ================================================================ listing
    def api_ls(self, method: str) -> None:
        query = self._query()
        path = clean_path(query.get("path"))
        if not os.path.isdir(path) and _archive_part(path):   # r41-files: an archive (or a folder in one) like a folder
            self._ls_archive(path)
            return
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        if not os.path.isdir(path):
            raise NotADirectoryError(path)
        stamp = fsops.dir_stamp(path)          # Round 39: taken BEFORE the listing — a change during it shows next poll
        handle_iter = fsops.iter_dir(path, chunk=int(query.get("chunk") or 3000))
        first = next(handle_iter, None)        # raises PermissionError before the 200 is sent
        self._stream_start()
        parent = os.path.dirname(path)
        head = {"ok": True, "path": path, "parent": parent if parent != path else "",
                "stamp": stamp, "writable": os.access(path, os.W_OK),
                "protected": fsops.protected_reason(path) or ""}
        if not self._line(head):
            return
        count = 0
        if first:
            count += len(first)
            if not self._line({"rows": first}):
                return
        for batch in handle_iter:
            count += len(batch)
            if not self._line({"rows": batch}):
                return
        self._line({"done": True, "count": count})

    def _ls_archive(self, path: str) -> None:
        from . import archive
        head, rows = archive.listing(path)          # raises before the 200 (missing inner folder, no 7-Zip…)
        found = archive.split(path) or (path, "")
        parent = os.path.dirname(path)
        self._stream_start()
        if not self._line({"ok": True, "path": path, "parent": parent if parent != path else "",
                           "stamp": _archive_stamp(found[0]), "writable": head["archive"]["writable"],
                           "protected": "", **head}):
            return
        if rows and not self._line({"rows": rows}):
            return
        self._line({"done": True, "count": len(rows)})

    def api_arc_info(self, method: str) -> None:     # r41-files: what the extract / add dialogs need to know
        from . import archive
        path = clean_path(self._query().get("path") if method == "GET" else self._json_body().get("path"))
        self._json({"ok": True, **archive.info(path)})

    def api_dirstamp(self, method: str) -> None:
        body = self._json_body() if method == "POST" else {"paths": [self._query().get("path")]}
        out = {}
        for raw in (body.get("paths") or [])[:20]:
            try:
                path = clean_path(raw)
                found = None if os.path.isdir(path) else _archive_part(path)
                out[raw] = _archive_stamp(found[0]) if found else fsops.dir_stamp(path)
            except (FsError, OSError):
                out[raw] = None
        self._json({"ok": True, "stamps": out})

    def api_sidebar(self, method: str) -> None:
        settings = settings_store.load()
        pinned = []
        for path in settings.get("pinned") or []:
            pinned.append({"path": path, "exists": os.path.isdir(path)})
        # r40-files-2: saved searches as «smart folders» in the sidebar (the file extras.saved_searches keeps)
        saved = [s for s in read_json(files_dir() / "saved_searches.json", []) if isinstance(s, dict) and s.get("query")]
        self._json({"ok": True, "drives": fsops.drives(), "places": fsops.places(), "pinned": pinned,
                    "tags": list(settings_store.tags_all().values()), "saved": saved[:30]})

    def api_complete(self, method: str) -> None:
        query = self._query()
        self._json({"ok": True, "items": fsops.complete(query.get("q", ""), show_hidden=query.get("hidden") == "1")})

    def api_stat(self, method: str) -> None:
        body = self._json_body()
        out = {}
        for raw in (body.get("paths") or [])[:500]:
            try:
                path = clean_path(raw)
                st = os.stat(path)
                out[raw] = {"exists": True, "is_dir": os.path.isdir(path), "size": st.st_size,
                            "mtime": int(st.st_mtime * 1000), "protected": fsops.protected_reason(path) or ""}
            except (FsError, OSError):
                out[raw] = {"exists": False}
        self._json({"ok": True, "items": out})

    def api_props(self, method: str) -> None:
        body = self._json_body()
        self._json({"ok": True, **fsops.properties(clean_path(body.get("path")))})

    def api_attrs(self, method: str) -> None:
        body = self._json_body()
        path = clean_path(body.get("path"))
        info = fsops.set_attributes(path, body.get("hidden") if "hidden" in body else None,
                                    body.get("readonly") if "readonly" in body else None)
        self._json({"ok": True, **info})

    # ================================================================ create / rename
    def api_mkdir(self, method: str) -> None:
        body = self._json_body()
        parent = clean_path(body.get("parent"))
        name = str(body.get("name") or "").strip()
        if body.get("unique"):
            name = fsops.unique_name(parent, name)
        path = fsops.make_folder(parent, name)
        self.app.queue.record("mkdir", [path], [("trash_created", path)], [path], label=name)
        self.app.events.push({"type": "changed", "paths": [parent]})
        self._json({"ok": True, "path": path})

    TEMPLATES = {".txt": b"", ".md": "# \n".encode(), ".html": b"<!doctype html>\n<html>\n<head><meta charset=\"utf-8\"><title></title></head>\n<body>\n</body>\n</html>\n",
                 ".json": b"{}\n", ".py": b"", ".csv": b"", ".js": b"", ".css": b""}

    def api_newfile(self, method: str) -> None:
        body = self._json_body()
        parent = clean_path(body.get("parent"))
        name = str(body.get("name") or "").strip()
        if body.get("unique"):
            name = fsops.unique_name(parent, name)
        ext = os.path.splitext(name)[1].lower()
        content = self.TEMPLATES.get(ext, b"")
        if ext == ".docx" or ext == ".xlsx" or ext == ".pptx":
            content = b""          # an empty Office file is invalid; Explorer's templates come from Office itself
        path = fsops.make_file(parent, name, content)
        self.app.queue.record("mkdir", [path], [("trash_created", path)], [path], label=name)
        self.app.events.push({"type": "changed", "paths": [parent]})
        self._json({"ok": True, "path": path})

    def api_rename(self, method: str) -> None:
        body = self._json_body()
        path = clean_path(body.get("path"))
        name = str(body.get("name") or "").strip()
        if name == os.path.basename(path):
            self._json({"ok": True, "path": path})
            return
        target = fsops.rename(path, name)
        self.app.queue.record("rename", [path], [("move_back", target, path)], [target], label=name)
        self.app.events.push({"type": "changed", "paths": [os.path.dirname(path)]})
        self._json({"ok": True, "path": target})

    def api_rename_preview(self, method: str) -> None:
        from .batchrename import preview
        body = self._json_body()
        paths = [clean_path(p) for p in (body.get("paths") or [])[:20000]]
        rows = preview(paths, body.get("rule") if isinstance(body.get("rule"), dict) else {})
        self._json({"ok": True, "items": rows, "errors": sum(1 for r in rows if r["error"]),
                    "changed": sum(1 for r in rows if r["changed"])})

    def api_rename_batch(self, method: str) -> None:
        from .batchrename import preview
        body = self._json_body()
        paths = [clean_path(p) for p in (body.get("paths") or [])[:20000]]
        rows = preview(paths, body.get("rule") if isinstance(body.get("rule"), dict) else {})
        bad = [r for r in rows if r["error"]]
        if bad:
            raise FsError("rename_conflicts", f"{len(bad)} names have problems")
        pairs = [[r["path"], r["new"]] for r in rows if r["changed"]]
        if not pairs:
            self._json({"ok": True, "op": None})
            return
        op = self.app.queue.submit("rename", [p for p, _ in pairs], "", pairs=pairs, label="rename")
        self._json({"ok": True, "op": op.snapshot()})

    # ================================================================ operations
    def api_ops(self, method: str) -> None:
        self._json({"ok": True, "ops": self.app.queue.list(), "undo": bool(self._undo_candidate())})

    def _undo_candidate(self) -> str:
        queue = self.app.queue
        for op_id in reversed(queue.undo_stack):
            op = queue.ops.get(op_id)
            if op and not op.undone and op.status in ("done", "cancelled", "error"):
                return op_id
        return ""

    def api_ops_submit(self, method: str) -> None:
        body = self._json_body()
        kind = str(body.get("kind") or "")
        if kind not in ("copy", "move", "trash", "delete", "extract", "compress", "download", "vpaste", "arcadd"):
            raise FsError("bad_kind", kind)
        if kind == "vpaste":                        # r40-files-core: virtual files on the Windows clipboard
            dest = clean_path(body.get("dest"))
            fsops.ensure_mutable(dest)
            op = self.app.queue.submit("vpaste", [], dest, count=int(body.get("count") or 0),
                                       label=str((body.get("args") or {}).get("label") or ""))
            self._json({"ok": True, "op": op.snapshot()})
            return
        if kind == "download":                      # r40-files-core: a link dropped from a browser
            url = str(body.get("url") or "")
            if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
                raise FsError("bad_url", url[:200])
            dest = clean_path(body.get("dest"))
            op = self.app.queue.submit("download", [url], dest, url=url,
                                       label=str((body.get("args") or {}).get("label") or ""))
            self._json({"ok": True, "op": op.snapshot()})
            return
        sources = [clean_path(p) for p in (body.get("sources") or [])[:100000]]
        if not sources:
            raise FsError("nothing_selected")
        dest = clean_path(body["dest"]) if body.get("dest") else ""
        if kind in ("trash", "delete", "move"):
            for path in sources:
                fsops.ensure_mutable(path)
        args = {k: v for k, v in (body.get("args") or {}).items()
                if k in ("copy_suffix", "on_conflict", "label", "members", "strip", "password", "format", "level", "inner")}   # r41-files: archives
        op = self.app.queue.submit(kind, sources, dest, **args)
        self._json({"ok": True, "op": op.snapshot()})

    def api_ops_control(self, method: str) -> None:
        body = self._json_body()
        action = str(body.get("action") or "")
        op_id = str(body.get("id") or "")
        queue = self.app.queue
        if action == "pause":
            queue.pause(op_id)
        elif action == "resume":
            queue.resume(op_id)
        elif action == "cancel":
            queue.cancel(op_id)
        elif action == "resolve":
            queue.resolve(op_id, str(body.get("choice") or ""), bool(body.get("all")), str(body.get("name") or ""))
        elif action == "clear":
            queue.clear_finished()
        elif action == "skip":                      # r40-files-core: skip the current file
            queue.skip(op_id)
        elif action == "retry":                     # r40-files-core: run what failed again
            self._json({"ok": True, "op": queue.retry(op_id).snapshot()})
            return
        else:
            raise FsError("bad_action", action)
        self._json({"ok": True})

    # r40-files-core: files dropped without a path (Explorer zip folders, Outlook, browsers) are streamed in
    def api_upload_start(self, method: str) -> None:
        body = json.loads(self._body(64 * 1024 * 1024) or b"{}")
        dest = clean_path(body.get("dest"))
        fsops.ensure_mutable(dest)
        items = []
        for raw in (body.get("items") or [])[:200000]:
            if isinstance(raw, dict) and raw.get("rel"):
                items.append({"rel": str(raw["rel"])[:1024], "size": max(0, int(raw.get("size") or 0)),
                              "mtime": max(0, int(raw.get("mtime") or 0)), "dir": bool(raw.get("dir"))})
        if not items:
            raise FsError("nothing_selected")
        tops = list(dict.fromkeys(it["rel"].replace("\\", "/").strip("/").split("/")[0] for it in items))
        op = self.app.queue.submit("upload", [os.path.join(dest, name) for name in tops], dest, items=items,
                                   label=str(body.get("label") or "")[:200])
        self._json({"ok": True, "op": op.snapshot()})

    def api_upload_prepare(self, method: str) -> None:
        body = self._json_body()
        self._json({"ok": True, **self.app.queue.upload_prepare(str(body.get("op") or ""), int(body.get("i") or 0))})

    def api_upload_put(self, method: str) -> None:
        query = self._query()
        self.close_connection = True               # a skipped / cancelled file leaves its body unread
        length = int(self.headers.get("Content-Length") or 0)
        result = self.app.queue.upload_put(str(query.get("op") or ""), int(query.get("i") or 0), self.rfile, length)
        self._json({"ok": True, **result})

    def api_upload_fail(self, method: str) -> None:
        body = self._json_body()
        self.app.queue.upload_fail(str(body.get("op") or ""), int(body.get("i") or 0), str(body.get("error") or ""))
        self._json({"ok": True})

    def api_ops_undo(self, method: str) -> None:
        body = self._json_body()
        op = self.app.queue.undo(str(body.get("id") or ""))
        self._json({"ok": True, "op": op.snapshot()})

    def api_ops_redo(self, method: str) -> None:      # r40-files-2: Ctrl+Y
        op = self.app.queue.redo()
        self._json({"ok": True, "op": op.snapshot()})

    def api_protected(self, method: str) -> None:
        body = self._json_body()
        out = {}
        for raw in (body.get("paths") or [])[:100000]:
            try:
                reason = fsops.protected_reason(clean_path(raw))
            except FsError:
                reason = "bad_path"
            if reason:
                out[raw] = reason
        self._json({"ok": True, "protected": out})

    # ================================================================ shell
    def api_open(self, method: str) -> None:
        body = self._json_body()
        path = clean_path(body.get("path"))
        if not os.path.exists(path) and _archive_part(path):   # r41-files: a file inside an archive opens from a cache copy
            path = _readable(path, body.get("password") or "")
        if path.lower().endswith(".lnk"):                  # Round 39: a folder shortcut opens in place, like Explorer
            target = fsops.lnk_target(path)
            if target and os.path.isdir(target):
                self._json({"ok": True, "navigate": target})
                return
        fsops.open_default(path)
        if os.path.isfile(path):
            from . import extras                   # Round 39: «Недавние»
            extras.note_recent(path)
        self._json({"ok": True})

    def _extras(self, op: str, method: str) -> None:
        from . import extras
        body = self._json_body() if method == "POST" else {}
        self._json(extras.handle(op, method, body, self._query()))

    def _feat40(self, op: str, method: str) -> None:   # r40-files-max
        from . import feat40
        body = self._json_body() if method == "POST" else {}
        self._json(feat40.handle(op, method, body, self._query()))

    def _hwnd(self) -> int:
        try:
            return int(self.app.desktop.hwnd()) if self.app.desktop is not None else 0
        except Exception:  # noqa: BLE001
            return 0

    def api_openwith(self, method: str) -> None:
        """GET ?path= → apps that can open the file; POST {path, handler?} → open with it / the system chooser."""
        from . import openwith
        if method == "GET":
            path = clean_path(self._query().get("path"))
            try:
                items = openwith.handlers(path)
            except openwith.OpenWithError as exc:
                log.info("open-with list failed: %s", exc)
                items = []
            self._json({"ok": True, "handlers": items, "supported": openwith.IS_WIN})
            return
        body = self._json_body()
        path = clean_path(body.get("path"))
        handler = str(body.get("handler") or "")
        if handler:
            try:
                openwith.launch(path, handler)
            except openwith.OpenWithError as exc:
                raise FsError(exc.code, str(exc)) from None
            self._json({"ok": True, "how": "handler"})
            return
        self._json({"ok": True, "how": fsops.open_with_dialog(path, self._hwnd())})

    def api_appicon(self, method: str) -> None:
        from . import openwith
        data = openwith.icon_png(str(self._query().get("id") or ""))
        if not data:
            self._error("no_icon", 404)
            return
        self._send(200, data, "image/png", {"Cache-Control": "private, max-age=86400"})

    def api_shellverb(self, method: str) -> None:
        body = self._json_body()
        verb = str(body.get("verb") or "")
        if verb not in ("properties", "runas", "print", "edit", "openas"):
            raise FsError("bad_verb", verb)
        fsops.shell_verb(clean_path(body.get("path")), verb, self._hwnd())
        self._json({"ok": True})

    def api_terminal(self, method: str) -> None:
        body = self._json_body()
        path = clean_path(body.get("path"))
        if not os.path.isdir(path):
            path = os.path.dirname(path)
        used = fsops.open_terminal(path, settings_store.load().get("terminal", "auto"))
        self._json({"ok": True, "terminal": used})

    def api_reveal(self, method: str) -> None:
        body = self._json_body()
        fsops.reveal_native(clean_path(body.get("path")))
        self._json({"ok": True})

    def api_osclip(self, method: str) -> None:
        """Windows clipboard interop: files copied in Explorer can be pasted here and vice versa."""
        from . import clip
        if method == "GET":
            self._json({"ok": True, **clip.read_files()})
            return
        body = self._json_body()
        paths = [clean_path(p) for p in (body.get("paths") or [])[:5000]]
        ok = clip.write_files(paths, cut=bool(body.get("cut")), owner=self._hwnd())
        self._json({"ok": True, "written": ok})

    # ================================================================ search
    def api_search(self, method: str) -> None:
        from . import search
        query_args = self._query()
        text = query_args.get("q", "")
        root = clean_path(query_args.get("root")) if query_args.get("root") else ""
        scope = query_args.get("scope", "folder")           # folder | everywhere
        show_hidden = query_args.get("hidden") == "1"
        limit = max(1, min(int(query_args.get("limit") or 5000), 50000))
        search_id = str(query_args.get("id") or uuid.uuid4().hex)[:40]
        query = search.Query(text)
        cancel = threading.Event()
        for old_id, old in list(self.app.searches.items()):      # one search at a time per page
            old.set()
            self.app.searches.pop(old_id, None)
        self.app.searches[search_id] = cancel
        self._stream_start()
        started = time.monotonic()
        try:
            if query.empty:
                self._line({"done": True, "count": 0, "source": "none"})
                return
            roots = None if scope == "everywhere" or not root else [root]
            source = "walk"
            rows = None
            # Only a READY index answers (Everything running / own index loaded): an idle or still-building
            # index returns [] instantly, which used to show "nothing found" for every «везде» search.
            if (scope == "everywhere" or root) and search.index_ready():
                rows = search.index_search(query, roots, limit)
                if rows is not None:
                    source = "index"
            if rows is not None and (rows or scope == "everywhere"):
                self._line({"source": source})
                for start in range(0, len(rows), 500):
                    if not self._line({"rows": rows[start:start + 500]}):
                        return
                self._line({"done": True, "count": len(rows), "source": source,
                            "ms": int((time.monotonic() - started) * 1000)})
                return
            if scope == "everywhere":
                roots_walk = [d["path"] for d in fsops.drives()] or [os.path.expanduser("~")]
            else:
                roots_walk = [root or os.path.expanduser("~")]
            self._line({"source": "walk"})
            batch: List[Dict[str, Any]] = []
            count = 0
            last_flush = time.monotonic()
            for walk_root in roots_walk:
                for row in search.walk_search(walk_root, query, cancel.is_set, limit - count, show_hidden):
                    batch.append(row)
                    count += 1
                    now = time.monotonic()
                    if len(batch) >= 400 or now - last_flush > 0.12:
                        if not self._line({"rows": batch}):
                            cancel.set()
                            return
                        batch, last_flush = [], now
                if count >= limit or cancel.is_set():
                    break
            if batch:
                self._line({"rows": batch})
            self._line({"done": True, "count": count, "source": "walk", "cancelled": cancel.is_set(),
                        "limited": count >= limit, "ms": int((time.monotonic() - started) * 1000)})
        finally:
            self.app.searches.pop(search_id, None)

    def api_search_stop(self, method: str) -> None:
        for cancel in self.app.searches.values():
            cancel.set()
        self._json({"ok": True})

    def api_search_status(self, method: str) -> None:
        from . import search
        self._json({"ok": True, "index": search.index_status()})

    # ================================================================ previews
    def api_thumb(self, method: str) -> None:
        from . import preview
        query = self._query()
        path = _readable(clean_path(query.get("path")))
        try:
            data, ctype = preview.thumbnail(path, int(query.get("s") or 256))
        except (FileNotFoundError, PermissionError):
            raise
        except Exception as exc:  # noqa: BLE001 - not an image Pillow can read
            self._error("no_thumbnail", 415, detail=str(exc)[:200])
            return
        self._send(200, data, ctype, {"Cache-Control": "private, max-age=86400"})

    def api_raw(self, method: str) -> None:
        query = self._query()
        path = _readable(clean_path(query.get("path")))
        size = os.path.getsize(path)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype in ("text/html", "image/svg+xml", "application/xhtml+xml", "text/javascript", "application/javascript"):
            ctype = "text/plain" if ctype != "image/svg+xml" else ctype
        start, end = 0, size - 1
        status = 200
        range_header = self.headers.get("Range") or ""
        match = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
        if match and size:
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else size - 1
            elif match.group(2):
                start = max(0, size - int(match.group(2)))
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            status = 206
        length = max(0, end - start + 1) if size else 0
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if ctype == "image/svg+xml":
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD" or not length:
            return
        with open(path, "rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                block = handle.read(min(1024 * 1024, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)

    def api_text(self, method: str) -> None:
        from . import preview
        query = self._query()
        path = _readable(clean_path(query.get("path")))
        limit = max(1024, min(int(query.get("max") or 256 * 1024), 4 * 1024 * 1024))
        self._json({"ok": True, **preview.read_text(path, limit)})

    def api_archive(self, method: str) -> None:
        from . import preview
        self._json({"ok": True, **preview.archive_list(_readable(clean_path(self._query().get("path"))))})

    def api_thumbs_cache(self, method: str) -> None:
        from . import preview
        if method == "POST":
            self._json({"ok": True, "removed": preview.clear_cache()})
            return
        self._json({"ok": True, **preview.cache_size()})

    # ================================================================ jobs
    def api_jobs_start(self, method: str) -> None:
        body = self._json_body()
        kind = str(body.get("kind") or "")
        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        if "path" in args:
            args["path"] = clean_path(args["path"])
        if "paths" in args:
            args["paths"] = [clean_path(p) for p in args["paths"][:10000]]
        job = self.app.jobs.start(kind, args)
        self._json({"ok": True, "job": job.snapshot()})

    def api_jobs_get(self, method: str) -> None:
        self._json({"ok": True, "job": self.app.jobs.get(self._query().get("id", "")).snapshot()})

    def api_jobs_cancel(self, method: str) -> None:
        body = self._json_body()
        self.app.jobs.get(str(body.get("id") or "")).cancel.set()
        self._json({"ok": True})

    # ================================================================ recycle bin
    def api_trash(self, method: str) -> None:
        from . import trash
        rows = trash.items()
        self._json({"ok": True, "items": rows, "size": sum(r.get("size") or 0 for r in rows)})

    def api_trash_restore(self, method: str) -> None:
        from . import trash
        body = self._json_body()
        restored, errors = [], []
        for token in (body.get("tokens") or [])[:5000]:
            try:
                restored.append(trash.restore(str(token)))
            except (trash.TrashError, OSError) as exc:
                errors.append({"token": token, "error": str(exc)})
        self.app.events.push({"type": "changed", "paths": sorted({os.path.dirname(p) for p in restored}) + ["::trash"]})
        self._json({"ok": True, "restored": restored, "errors": errors})

    def api_trash_purge(self, method: str) -> None:
        from . import trash
        body = self._json_body()
        count = trash.purge([str(t) for t in (body.get("tokens") or [])[:5000]])
        self.app.events.push({"type": "changed", "paths": ["::trash"]})
        self._json({"ok": True, "removed": count})

    def api_trash_empty(self, method: str) -> None:
        from . import trash
        count = trash.empty()
        self.app.events.push({"type": "changed", "paths": ["::trash"]})
        self._json({"ok": True, "removed": count})

    # ================================================================ tags
    def api_tags(self, method: str) -> None:
        if method == "POST":
            body = self._json_body()
            rows = []
            for raw in (body.get("paths") or [])[:5000]:
                path = clean_path(raw)
                colors = body.get("colors")
                if body.get("toggle"):
                    current = settings_store.tags_all().get(os.path.normcase(os.path.normpath(path)), {})
                    have = set(current.get("colors") or [])
                    color = str(body["toggle"])
                    colors = sorted(have ^ {color})
                rows.append(settings_store.tags_set(path, colors=colors, favorite=body.get("favorite"),
                                                    note=body.get("note")))
            self._json({"ok": True, "items": rows, "tags": list(settings_store.tags_all().values())})
            return
        self._json({"ok": True, "tags": list(settings_store.tags_all().values())})

    # ================================================================ AI
    def api_ai_status(self, method: str) -> None:
        settings = settings_store.load()
        try:
            from ..memunote import ai
            value = ai.status(settings.get("proxy_url", ""), force=self._query().get("force") == "1")
        except Exception as exc:  # noqa: BLE001
            value = {"online": False, "models": [], "error": f"{type(exc).__name__}: {exc}"[:200]}
        self._json({"ok": True, "enabled": settings.get("ai_enabled", True), "model": settings.get("ai_model", ""),
                    **value})

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
        from . import assistant
        body = self._json_body()
        settings = settings_store.load()
        if not settings.get("ai_enabled", True):
            self._error("ai_disabled", 403)
            return
        scope = clean_path(body.get("scope") or os.path.expanduser("~"))
        if not os.path.isdir(scope):
            raise NotADirectoryError(scope)
        llm = getattr(self.app, "llm_override", None)
        cancel = threading.Event()
        holder: Dict[str, Any] = {"cancel": cancel, "conn": None}
        model = str(body.get("model") or settings.get("ai_model") or "")
        if llm is None:
            from ..memunote import ai
            if not model:
                model = ai.status(settings.get("proxy_url", "")).get("default_model") or ""
            if not model:
                self._error("no_model", 503)
                return
            llm = assistant.proxy_llm(model, settings, cancel, holder)
        # r40-files-max: documents for «sort by meaning» are read by the fast model (Settings → AI), else the chat model
        fast = str(settings.get("ai_fast_model") or "")
        classifier = assistant.proxy_llm(fast, settings, cancel, holder) if fast and getattr(self.app, "llm_override", None) is None else None
        request_id = str(body.get("id") or uuid.uuid4().hex)[:64]
        self.app.streams[request_id] = holder
        self._stream_start("text/event-stream; charset=utf-8")

        def emit(event: Dict[str, Any]) -> bool:
            try:
                self.wfile.write(b"data: " + json.dumps(event, ensure_ascii=False, default=str).encode("utf-8") + b"\n\n")
                self.wfile.flush()
                return True
            except OSError:
                cancel.set()
                return False
        emit({"t": "start", "id": request_id, "model": model, "scope": scope})
        history = [m for m in body.get("messages") or [] if isinstance(m, dict)]
        context = ""
        selection = [str(p) for p in (body.get("selection") or [])[:50]]
        if selection:
            context = ("Выделенные пользователем элементы:\n" if self.app.lang() != "en" else "Items selected by the user:\n") \
                + "\n".join(selection)
        try:
            assistant.run_chat(scope, history, self.app.lang(), llm, emit, self.app.plans, cancel, context, classifier)
            emit({"t": "done", "cancelled": cancel.is_set()})
        except Exception as exc:  # noqa: BLE001
            kind = getattr(exc, "kind", "internal")
            if not cancel.is_set():
                log.warning("ai chat failed: %s", exc)
                emit({"t": "error", "message": f"{exc}"[:500], "kind": kind})
        finally:
            self.app.streams.pop(request_id, None)

    def api_ai_execute(self, method: str) -> None:
        from . import assistant
        body = self._json_body()
        plan = self.app.plans.get(str(body.get("plan_id") or ""))
        result = assistant.execute_plan(plan, body.get("selected") or [], self.app.queue,
                                        permanent=bool(body.get("permanent")))
        self._json({"ok": True, **result})

    # ================================================================ window / remote
    def api_window(self, method: str) -> None:
        body = self._json_body()
        desktop = self.app.desktop
        if desktop is not None:
            desktop.window_command(str(body.get("cmd") or ""), body)
        self._json({"ok": True})

    def api_remote(self, method: str) -> None:
        body = self._json_body()
        action = body.get("action")
        if action == "open":
            paths = [str(p) for p in body.get("paths") or [] if isinstance(p, str)][:50]
            reveal = [p for p in (body.get("reveal") or []) if p in paths]           # r41-files
            shell = list(paths) if body.get("shell") is True else []
            browse = list(paths) if body.get("browse") is True else []
            if body.get("require_ui") and not _ui_alive(self.app):
                # Round 39: one-request hand-off (desktop.hand_off / the registry launcher) — a ghost instance says no
                self._json({"ok": True, "taken": False, "pid": os.getpid(), "ui": self.app.ui,
                            "age": round(time.time() - self.app.started, 1)})
                return
            if not self.app.booted:          # Round 39: the page has not booted yet — it takes them with the start-up paths
                self.app.initial_paths.extend(p for p in paths if p not in self.app.initial_paths)
                self.app.initial_reveal.extend(reveal)
                self.app.initial_shell.extend(shell)
                self.app.initial_browse.extend(browse)
            else:
                self.app.events.push({"type": "open", "paths": paths, "info": path_info(paths), "reveal": reveal, "shell": shell,
                                      "browse": browse})
            _activate(self.app)
        elif action == "shutdown":
            self.app.events.push({"type": "shutdown"})
            if self.app.desktop is not None:
                threading.Timer(1.0, self.app.desktop.force_close).start()
            else:
                threading.Timer(0.5, self.app.stopping.set).start()
        elif action == "activate":
            _activate(self.app)
        elif action == "settings":                      # Round 38: changed in MemuAI → the page re-reads and applies
            self.app.events.push({"type": "settings"})
            _warm_settings_changed(self.app)
            _sync_archive_assoc()                        # r41-files: «Open archives in MemuFiles» switched in MemuAI
        elif action == "state":                         # r41-files: Quick Access (MemuAI) asks what this window shows
            self._json({"ok": True, "taken": False, "pid": os.getpid(), "ui": self.app.ui, "state": _public_state(self.app)})
            return
        self._json({"ok": True, "taken": action == "open", "pid": os.getpid(), "ui": self.app.ui,
                    "age": round(time.time() - self.app.started, 1)})


def _activate(app: FilesApp) -> None:
    """Show/raise the window; a window kept warm in the background (Round 39 ``keep_running``) comes back."""
    if app.desktop is None:
        return
    app.desktop.window_command("activate", {})
    if app.ui == "warm":
        app.ui = "window"


def _warm_settings_changed(app: FilesApp) -> None:
    """``keep_running`` switched off while the window is hidden in the background: quit for real."""
    if app.ui == "warm" and app.desktop is not None and not settings_store.load().get("keep_running"):
        app.stopping.set()
        threading.Timer(0.3, app.desktop.force_close).start()


STARTING_GRACE = 45.0       # = app.memunote.desktop.STARTING_GRACE: a window that never showed in time is a ghost


def _ui_alive(app: FilesApp) -> bool:
    return app.ui in ("window", "browser", "headless", "warm") or (
        app.ui == "starting" and time.time() - app.started < STARTING_GRACE)


def _public_state(app: FilesApp) -> Dict[str, Any]:
    state = dict(app.ui_state or {})
    state["pid"] = os.getpid()
    state["hwnd"] = 0
    if app.desktop is not None:
        try:
            state["hwnd"] = int(app.desktop.hwnd())
        except Exception:  # noqa: BLE001
            pass
    return state


def _migrate_views(settings: Dict[str, Any]) -> Dict[str, Any]:
    """r41-files, once: Downloads remembered by an older version with name order / no groups gets Explorer's look
    (newest first, grouped by date) — the user's view (icons, details…) is kept."""
    if int(settings.get("views_rev") or 0) >= 41:
        return settings
    views = dict(settings.get("view_by_folder") or {})
    down = known_folders().get("downloads") or ""
    if down:
        folded = down.rstrip("\\/").lower() if os.name == "nt" else down.rstrip("/")
        for key in list(views):
            memo = views[key]
            k = key.rstrip("\\/").lower() if os.name == "nt" else key.rstrip("/")
            if k == folded and isinstance(memo, dict) and memo.get("group") != "date":
                views[key] = {k2: v for k2, v in memo.items() if k2 not in ("sort", "group")}
    return settings_store.save({"view_by_folder": views, "views_rev": 41})


def _sync_archive_assoc() -> None:
    """r41-files: «Open archives in MemuFiles» → the per-user file association (Windows; no-op elsewhere)."""
    if os.name != "nt":
        return
    from . import archive_assoc
    try:
        archive_assoc.sync(bool(settings_store.load().get("open_archives")))
    except Exception:  # noqa: BLE001
        log.exception("archive association failed")


def _archive_part(path: str) -> Optional[Tuple[str, str]]:
    """r41-files: ``(archive, inner)`` for a path that is an archive or goes through one (cheap name check first)."""
    from . import archive
    low = path.lower()
    if not any(ext in low for ext in (".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".cab", ".iso",
                                       ".jar", ".apk", ".tbz", ".txz", ".arj", ".lzh", ".wim", ".cb", ".epub", ".whl",
                                       ".nupkg", ".xpi", ".lz", ".z")):
        return None
    return archive.split(path)


def _archive_stamp(path: str) -> str:
    try:
        st = os.stat(path)
        return f"arc:{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return ""


def _readable(path: str, password: str = "") -> str:
    """r41-files: read-only handlers (raw, thumb, text, open) accept files inside archives — a cached real copy."""
    if os.path.exists(path) or not _archive_part(path):
        return path
    from . import archive
    return archive.materialize(path, password)


def path_info(paths: List[str]) -> Dict[str, Dict[str, bool]]:
    """Round 39: ``{path: {exists, is_dir}}`` for the folders/files a start or a hand-off brings, so the page opens the
    tab at once instead of asking ``/api/stat`` first (one round trip less on the Win+E / double-click path)."""
    out: Dict[str, Dict[str, bool]] = {}
    for path in paths[:50]:
        try:
            out[path] = {"exists": os.path.exists(path), "is_dir": os.path.isdir(path)}
        except (OSError, ValueError):
            out[path] = {"exists": False, "is_dir": False}
    return out


_KNOWN: Dict[str, str] = {}


def known_folders() -> Dict[str, str]:
    """Round 39: ``{id: path}`` of the known folders (Downloads, Documents…, Windows «Recent») — the page gives them
    Explorer's default look (Downloads: newest first). Resolved once per process."""
    if not _KNOWN:
        try:
            _KNOWN.update({row["id"]: row["path"] for row in fsops.places() if row.get("path")})
        except Exception:  # noqa: BLE001
            log.debug("known folders unavailable", exc_info=True)
        if os.name == "nt" and os.environ.get("APPDATA"):
            _KNOWN["recent"] = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Recent")
    return dict(_KNOWN)


def _deferred_warm(app: FilesApp, delay: float = 2.5) -> None:
    """Round 39: after the first page boot (not at process start, where it fought the first paint for the CPU/GIL):
    build the operation queue/jobs and load MemuAI's saved file index so the first «везде» search is instant."""
    if getattr(app, "_warm_started", False):
        return
    app._warm_started = True

    def _run() -> None:
        if app.stopping.wait(delay):
            return
        try:
            app.queue, app.jobs                     # noqa: B018 - build the lazy members off the request path
        except Exception:  # noqa: BLE001
            log.debug("lazy members failed", exc_info=True)
        try:
            from . import search
            search.index_ready()
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=_run, name="memufiles-warm", daemon=True).start()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 64


def start(app: FilesApp, port: int = 0) -> Tuple[Server, threading.Thread]:
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = Server(("127.0.0.1", port), handler)
    app.port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, name="memufiles-http", daemon=True)
    thread.start()
    return server, thread
