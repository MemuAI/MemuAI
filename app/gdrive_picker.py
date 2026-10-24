"""Round 29: «Добавить с Google Диска» / «Add from Google Drive» — the composer «+» menu.

Browses the user's Google Drive (My Drive folders, recent, shared with me,
starred, search) and downloads the chosen files so the chat attaches them
exactly like local files.  Google Docs / Sheets / Slides / Drawings are
exported (DOCX / PDF / TXT / Markdown, a workbook becomes Markdown tables, …).

Account and scopes
------------------
The Google account is the ONE account of MemuAI (Round 30: :mod:`app.google_account`
— Settings → Integrations → Google and Settings → Sync are two views of it;
:mod:`app.gdrive_sync_drive` holds the grant).  Backups need only
``drive.appdata`` + ``drive.file``; listing and reading the user's *own* files
needs ``drive.readonly``, so that scope is requested **incrementally** — only
when the user opens this picker for the first time and agrees (Google's
``include_granted_scopes``), unless Google Drive was already switched on in
Integrations (it grants ``drive.readonly``).  Google Picker (``drive.file`` only) would need a browser API key
and Google's JS widget inside the app window, which an installed desktop app
with a user-supplied OAuth client cannot rely on.

Tokens never leave this module: responses carry only file metadata and file
bytes.  Every HTTP call goes through one replaceable ``httpx`` transport
(``tests/round29_gdrive_picker_test.py`` runs against an in-memory fake Drive).
"""
from __future__ import annotations

import logging
import re
import threading
import time
import urllib.parse
from collections import OrderedDict
from pathlib import PurePath
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

try:  # FastAPI resolves the string annotations of the routes from module globals
    from fastapi import Request
except ImportError:  # pragma: no cover - not every bundle has FastAPI
    Request = None  # type: ignore[assignment,misc]

log = logging.getLogger("memuai.gdrive.picker")

API = "https://www.googleapis.com/drive/v3"
SCOPE_READONLY = "https://www.googleapis.com/auth/drive.readonly"
#: Scopes that already allow reading any file of the user.
READ_SCOPES = {SCOPE_READONLY, "https://www.googleapis.com/auth/drive"}
FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"
MAX_FILE = 5 * 1024 * 1024          # the chat's limit for one attachment (app.js addAttachmentFiles)
MAX_THUMB = 600 * 1024
PAGE_SIZE = 100
_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_.~=+/-]{1,2000}$")
VIEWS = ("folder", "recent", "shared", "starred", "search")
TYPES = ("all", "docs", "sheets", "slides", "pdf", "images", "folders")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

#: Google-native type → {format: (export mime, extension)}; the first format is the default.
EXPORTS: Dict[str, Dict[str, Tuple[str, str]]] = {
    "application/vnd.google-apps.document": {"docx": (DOCX, ".docx"), "pdf": ("application/pdf", ".pdf"),
                                             "md": ("text/markdown", ".md"), "txt": ("text/plain", ".txt")},
    "application/vnd.google-apps.spreadsheet": {"md": (XLSX, ".md"), "xlsx": (XLSX, ".xlsx"),
                                                "csv": ("text/csv", ".csv"), "pdf": ("application/pdf", ".pdf")},
    "application/vnd.google-apps.presentation": {"pdf": ("application/pdf", ".pdf"), "txt": ("text/plain", ".txt"),
                                                 "pptx": (PPTX, ".pptx")},
    "application/vnd.google-apps.drawing": {"png": ("image/png", ".png"), "pdf": ("application/pdf", ".pdf")},
    "application/vnd.google-apps.script": {"json": ("application/vnd.google-apps.script+json", ".json")},
}

_KIND_BY_MIME = {
    FOLDER: "folder", "application/vnd.google-apps.document": "doc", "application/vnd.google-apps.spreadsheet": "sheet",
    "application/vnd.google-apps.presentation": "slides", "application/vnd.google-apps.drawing": "drawing",
    "application/vnd.google-apps.form": "form", "application/pdf": "pdf", DOCX: "doc", "application/msword": "doc",
    XLSX: "sheet", "application/vnd.ms-excel": "sheet", "text/csv": "sheet", PPTX: "slides",
    "application/vnd.ms-powerpoint": "slides", "application/zip": "archive", "application/x-zip-compressed": "archive",
    "application/json": "code", "text/markdown": "text", "text/plain": "text", SHORTCUT: "shortcut",
}
_CODE_EXT = {".py", ".js", ".ts", ".json", ".html", ".css", ".java", ".c", ".cpp", ".cs", ".go", ".rs", ".rb",
             ".php", ".sh", ".ps1", ".yaml", ".yml", ".toml", ".xml", ".sql", ".kt", ".swift"}


class PickerError(RuntimeError):
    def __init__(self, ru: str, en: str = "", *, code: str = "", status: int = 400) -> None:
        super().__init__(ru)
        self.ru, self.en, self.code, self.status = ru, en or ru, code, status


def kind_of(mime: str, name: str = "") -> str:
    mime = str(mime or "")
    if mime in _KIND_BY_MIME:
        return _KIND_BY_MIME[mime]
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    ext = PurePath(str(name or "")).suffix.lower()
    if ext in _CODE_EXT:
        return "code"
    if mime.startswith("text/"):
        return "text"
    if mime.startswith("application/vnd.google-apps."):
        return "google"
    return "file"


def quote_q(value: str) -> str:
    """A string literal of the Drive query language."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def safe_name(name: str, ext: str = "") -> str:
    base = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", " ", str(name or "")).strip().strip(".") or "file"
    base = base[:150]
    if ext and not base.lower().endswith(ext):
        base += ext
    return base


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def sheets_markdown(name: str, data: bytes) -> bytes:
    """An .xlsx workbook → Markdown tables (every visible sheet): what the chat can read."""
    from .tables_api import parse_xlsx, XlsxError
    try:
        book = parse_xlsx(data)
    except XlsxError as exc:
        raise PickerError(f"Таблица не прочитана: {exc}", f"The spreadsheet could not be read: {exc}", code="xlsx") from exc

    def cell(value: Any) -> str:
        return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").strip()

    parts = [f"# {name}"]
    for sheet in book.get("sheets") or []:
        rows = [row for row in sheet.get("rows") or [] if any(str(c or "").strip() for c in row)]
        parts.append(f"\n## {sheet.get('name') or 'Sheet'}\n")
        if not rows:
            parts.append("_(empty)_")
            continue
        width = max(len(row) for row in rows)
        rows = [list(row) + [""] * (width - len(row)) for row in rows]
        parts.append("| " + " | ".join(cell(c) for c in rows[0]) + " |")
        parts.append("|" + "---|" * width)
        parts.extend("| " + " | ".join(cell(c) for c in row) + " |" for row in rows[1:])
    if book.get("truncated"):
        parts.append("\n_(the workbook was truncated)_")
    return ("\n".join(parts) + "\n").encode("utf-8")


class Picker:
    """Drive browsing for the picker dialog. ``sync`` returns the SyncService (or None)."""

    def __init__(self, sync: Callable[[], Any], *, transport: Optional[httpx.BaseTransport] = None) -> None:
        self.sync = sync
        self.transport = transport
        self._thumbs: "OrderedDict[str, Tuple[str, bytes]]" = OrderedDict()
        self._lock = threading.Lock()

    # ----------------------------------------------------------- account
    def _auth(self):
        svc = self.sync()
        return getattr(svc, "auth", None) if svc is not None else None

    def status(self) -> Dict[str, Any]:
        auth = self._auth()
        out: Dict[str, Any] = {"scope": SCOPE_READONLY, "max_file": MAX_FILE}
        has_client = bool(auth and auth.client()[0])
        connected = bool(auth and auth.connected())
        out["has_client"] = has_client
        out["flow"] = auth.flow_status() if auth else {"state": "idle"}
        if connected:
            try:
                account = (self.sync().state() or {}).get("account") or {}
            except Exception:
                account = {}
            out["account"] = {"email": str(account.get("email") or ""), "name": str(account.get("name") or "")}
            if auth.granted_scopes() & READ_SCOPES:
                return {**out, "state": "ready", "source": "sync"}
        if connected:
            return {**out, "state": "need_consent", "source": "sync"}
        return {**out, "state": "not_connected" if has_client else "no_client", "source": ""}

    def authorize(self) -> Dict[str, Any]:
        auth = self._auth()
        if auth is None or not auth.client()[0]:
            raise PickerError("Сначала подключите Google в «Настройки → Интеграции → Google»",
                              "Connect Google in Settings → Integrations → Google first", code="no_client")
        return auth.start_flow(extra_scopes=(SCOPE_READONLY,), purpose="picker")

    def _token_fn(self) -> Callable[[bool], str]:
        auth = self._auth()
        if auth is not None and auth.connected() and auth.granted_scopes() & READ_SCOPES:
            return lambda force=False: auth.access_token(force=force)
        if auth is not None and auth.connected():
            raise PickerError("Разрешите MemuAI читать файлы Google Диска — один раз",
                              "Allow MemuAI to read your Google Drive files — once", code="need_consent", status=403)
        raise PickerError("Google Диск не подключён", "Google Drive is not connected", code="not_connected", status=403)

    # -------------------------------------------------------------- HTTP
    def _request(self, method: str, url: str, *, stream_limit: int = 0, **kwargs: Any) -> Tuple[httpx.Response, bytes]:
        token_fn = self._token_fn()
        for attempt in range(3):
            token = token_fn(attempt > 0)
            headers = dict(kwargs.pop("headers", {}) or {})
            headers["Authorization"] = "Bearer " + token
            try:
                with httpx.Client(timeout=httpx.Timeout(60, connect=15), transport=self.transport,
                                  follow_redirects=True) as client:
                    with client.stream(method, url, headers=headers, **kwargs) as response:
                        body = b""
                        if response.status_code < 400 and stream_limit:
                            chunks: List[bytes] = []
                            size = 0
                            for chunk in response.iter_bytes():
                                size += len(chunk)
                                if size > stream_limit:
                                    raise PickerError(
                                        f"Файл больше {stream_limit // (1024 * 1024)} МБ — так много к сообщению не прикрепить",
                                        f"The file is larger than {stream_limit // (1024 * 1024)} MB — too big to attach",
                                        code="too_big", status=413)
                                chunks.append(chunk)
                            body = b"".join(chunks)
                        else:
                            body = response.read()
            except httpx.HTTPError as exc:
                raise PickerError("Нет связи с Google Диском", "Cannot reach Google Drive", code="network", status=502) from exc
            if response.status_code == 401 and attempt == 0:
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(0.4 * (attempt + 1))
                continue
            if response.status_code >= 400:
                raise self._error(response, body)
            return response, body
        raise PickerError("Google Диск не ответил", "Google Drive did not answer", code="network", status=502)

    def _json(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        import json
        _, body = self._request("GET", url, params=params)
        try:
            data = json.loads(body.decode("utf-8") or "{}")
        except ValueError as exc:
            raise PickerError("Google Диск вернул непонятный ответ", "Google Drive returned an unreadable answer") from exc
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _error(response: httpx.Response, body: bytes) -> PickerError:
        import json
        try:
            error = (json.loads(body.decode("utf-8")) or {}).get("error") or {}
        except Exception:
            error = {}
        reasons = {str(row.get("reason") or "") for row in (error.get("errors") or []) if isinstance(row, dict)}
        message = str(error.get("message") or "")[:200]
        status = response.status_code
        if status == 401:
            return PickerError("Доступ к Google Диску истёк — подключите аккаунт заново",
                               "Google Drive access expired — connect the account again", code="auth", status=401)
        if status == 403 and (reasons & {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}
                              or "scope" in message.lower() or "insufficient" in message.lower()):
            return PickerError("Разрешите MemuAI читать файлы Google Диска — один раз",
                               "Allow MemuAI to read your Google Drive files — once", code="need_consent", status=403)
        if status == 403 and reasons & {"cannotDownloadFile", "cannotExportFile"}:
            return PickerError("Владелец запретил скачивание этого файла", "The owner disabled downloading this file",
                               code="forbidden", status=403)
        if status == 403 and "exportSizeLimitExceeded" in reasons:
            return PickerError("Документ слишком большой для экспорта Google (больше 10 МБ)",
                               "The document is too large for Google's export (over 10 MB)", code="too_big", status=413)
        if status == 404:
            return PickerError("Файл не найден или к нему нет доступа", "The file was not found or is not shared with you",
                               code="not_found", status=404)
        if status == 429 or "rateLimitExceeded" in reasons or "userRateLimitExceeded" in reasons:
            return PickerError("Google просит подождать — слишком много запросов", "Google asks to slow down — too many requests",
                               code="rate", status=429)
        return PickerError(f"Google Диск ответил ошибкой {status}" + (f": {message}" if message else ""),
                           f"Google Drive answered {status}" + (f": {message}" if message else ""), status=502)

    # ------------------------------------------------------------ listing
    @staticmethod
    def item(row: Dict[str, Any]) -> Dict[str, Any]:
        mime = str(row.get("mimeType") or "")
        name = str(row.get("name") or "")[:300]
        target = row.get("shortcutDetails") or {}
        real_mime = str(target.get("targetMimeType") or mime) if mime == SHORTCUT else mime
        kind = kind_of(real_mime, name)
        exports = EXPORTS.get(real_mime)
        caps = row.get("capabilities") or {}
        size = _int(row.get("size"))
        owners = row.get("owners") or []
        owner = next((str(o.get("displayName") or "") for o in owners if isinstance(o, dict)), "")
        attachable = kind != "folder" and (bool(exports) or not real_mime.startswith("application/vnd.google-apps."))
        return {
            "id": str(row.get("id") or ""),
            "target": str(target.get("targetId") or "") if mime == SHORTCUT else "",
            "name": name, "mime": real_mime, "kind": kind, "size": size,
            "modified": str(row.get("modifiedTime") or ""), "viewed": str(row.get("viewedByMeTime") or ""),
            "owner": owner[:120], "mine": any(bool(o.get("me")) for o in owners if isinstance(o, dict)),
            "google": real_mime.startswith("application/vnd.google-apps.") and kind != "folder",
            "formats": list(exports.keys()) if exports else [],
            "too_big": bool(size and size > MAX_FILE and not exports),
            "attachable": attachable and caps.get("canDownload", True) is not False,
            "thumb": bool(row.get("thumbnailLink")) and kind in ("image", "pdf", "doc", "slides", "sheet", "drawing", "video"),
            "shortcut": mime == SHORTCUT,
        }

    def list(self, *, view: str = "folder", folder: str = "root", query: str = "", kind: str = "all",
             page_token: str = "", order: str = "") -> Dict[str, Any]:
        view = view if view in VIEWS else "folder"
        kind = kind if kind in TYPES else "all"
        folder = folder if folder == "root" or _ID.match(str(folder or "")) else "root"
        query = re.sub(r"\s+", " ", str(query or "")).strip()[:200]
        clauses = ["trashed = false"]
        order_by = "folder,name_natural"
        if order == "modified":
            order_by = "folder,modifiedTime desc"
        elif order == "name":
            order_by = "folder,name_natural"
        if view == "folder":
            clauses.append(f"{quote_q(folder)} in parents")
        elif view == "recent":
            clauses.append(f"mimeType != {quote_q(FOLDER)}")
            order_by = "viewedByMeTime desc"
        elif view == "shared":
            clauses.append("sharedWithMe = true")
            order_by = "sharedWithMeTime desc" if not order else order_by
        elif view == "starred":
            clauses.append("starred = true")
        elif view == "search":
            if len(query) < 1:
                return {"files": [], "next": ""}
            clauses.append(f"(name contains {quote_q(query)} or fullText contains {quote_q(query)})")
            order_by = ""   # Drive refuses to sort fullText results: they come by relevance
        types = {
            "docs": ["application/vnd.google-apps.document", DOCX, "application/msword", "text/plain", "text/markdown"],
            "sheets": ["application/vnd.google-apps.spreadsheet", XLSX, "application/vnd.ms-excel", "text/csv"],
            "slides": ["application/vnd.google-apps.presentation", PPTX, "application/vnd.ms-powerpoint"],
            "pdf": ["application/pdf"],
            "folders": [FOLDER],
        }.get(kind)
        if kind == "images":
            clauses.append("mimeType contains 'image/'")
        elif types:
            mimes = list(types) + ([FOLDER] if view == "folder" and kind != "folders" else [])
            clauses.append("(" + " or ".join(f"mimeType = {quote_q(m)}" for m in mimes) + ")")
        params: Dict[str, Any] = {
            "q": " and ".join(clauses), "pageSize": PAGE_SIZE, "spaces": "drive",
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
            "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime,viewedByMeTime,thumbnailLink,"
                      "owners(displayName,me),shortcutDetails(targetId,targetMimeType),capabilities(canDownload))",
        }
        if order_by:
            params["orderBy"] = order_by
        if page_token and _TOKEN.match(page_token):
            params["pageToken"] = page_token
        data = self._json(API + "/files", params)
        files = [self.item(row) for row in data.get("files") or [] if isinstance(row, dict) and row.get("id")]
        return {"files": files, "next": str(data.get("nextPageToken") or "")}

    def folder_path(self, folder: str) -> List[Dict[str, str]]:
        """Breadcrumbs from My Drive to ``folder`` (at most 12 levels)."""
        crumbs: List[Dict[str, str]] = []
        current = folder
        for _ in range(12):
            if current == "root" or not _ID.match(str(current or "")):
                break
            meta = self._json(API + "/files/" + current, {"fields": "id,name,parents", "supportsAllDrives": "true"})
            crumbs.append({"id": str(meta.get("id") or current), "name": str(meta.get("name") or "")[:200]})
            parents = meta.get("parents") or []
            if not parents:
                break
            current = str(parents[0])
        crumbs.reverse()
        return crumbs

    # ----------------------------------------------------------- download
    def download(self, file_id: str, fmt: str = "") -> Dict[str, Any]:
        if not _ID.match(str(file_id or "")):
            raise PickerError("Неверный идентификатор файла", "Invalid file id", code="bad_id")
        fields = "id,name,mimeType,size,shortcutDetails(targetId,targetMimeType),capabilities(canDownload)"
        meta = self._json(API + "/files/" + file_id, {"fields": fields, "supportsAllDrives": "true"})
        if meta.get("mimeType") == SHORTCUT:
            target = str((meta.get("shortcutDetails") or {}).get("targetId") or "")
            if not _ID.match(target):
                raise PickerError("Ярлык ведёт в никуда", "The shortcut points nowhere", code="not_found", status=404)
            meta = self._json(API + "/files/" + target, {"fields": fields, "supportsAllDrives": "true"})
        mime = str(meta.get("mimeType") or "")
        name = str(meta.get("name") or "file")
        real_id = str(meta.get("id") or file_id)
        if mime == FOLDER:
            raise PickerError("Папку нельзя прикрепить — откройте её и выберите файлы",
                              "A folder cannot be attached — open it and pick files", code="folder")
        if (meta.get("capabilities") or {}).get("canDownload") is False:
            raise PickerError("Владелец запретил скачивание этого файла", "The owner disabled downloading this file",
                              code="forbidden", status=403)
        exports = EXPORTS.get(mime)
        if exports:
            fmt = fmt if fmt in exports else next(iter(exports))
            export_mime, ext = exports[fmt]
            _, data = self._request("GET", API + "/files/" + real_id + "/export", params={"mimeType": export_mime},
                                    stream_limit=MAX_FILE * 3 if fmt == "md" and export_mime == XLSX else MAX_FILE)
            note = "exported"
            out_mime = export_mime
            if fmt == "md" and export_mime == XLSX:
                data, out_mime, note = sheets_markdown(name, data), "text/markdown", "sheets_md"
            if len(data) > MAX_FILE:
                raise PickerError("После экспорта файл больше 5 МБ — выберите другой формат",
                                  "The exported file is larger than 5 MB — choose another format", code="too_big", status=413)
            return {"name": safe_name(name, ext), "mime": out_mime.split(";")[0], "data": data, "note": note, "format": fmt}
        if mime.startswith("application/vnd.google-apps."):
            raise PickerError("Этот тип файлов Google (формы, сайты, карты) не скачивается",
                              "This kind of Google file (forms, sites, maps) cannot be downloaded", code="unsupported")
        size = _int(meta.get("size"))
        if size > MAX_FILE:
            raise PickerError(f"Файл больше 5 МБ ({size / 1048576:.1f} МБ) — так много к сообщению не прикрепить",
                              f"The file is larger than 5 MB ({size / 1048576:.1f} MB) — too big to attach",
                              code="too_big", status=413)
        _, data = self._request("GET", API + "/files/" + real_id, params={"alt": "media", "supportsAllDrives": "true"},
                                stream_limit=MAX_FILE)
        lower = name.lower()
        if (mime == XLSX or lower.endswith(".xlsx")) and fmt != "raw":
            # The chat reads text out of DOCX / PDF / text, not out of workbooks: a table becomes Markdown.
            return {"name": safe_name(PurePath(name).stem, ".md"), "mime": "text/markdown",
                    "data": sheets_markdown(name, data), "note": "sheets_md", "format": "md"}
        return {"name": safe_name(name), "mime": mime or "application/octet-stream", "data": data, "note": "", "format": ""}

    def thumbnail(self, file_id: str) -> Tuple[str, bytes]:
        if not _ID.match(str(file_id or "")):
            raise PickerError("Неверный идентификатор файла", "Invalid file id", code="bad_id")
        with self._lock:
            if file_id in self._thumbs:
                self._thumbs.move_to_end(file_id)
                return self._thumbs[file_id]
        meta = self._json(API + "/files/" + file_id, {"fields": "thumbnailLink", "supportsAllDrives": "true"})
        link = str(meta.get("thumbnailLink") or "")
        host = urllib.parse.urlsplit(link).hostname or ""
        if not link.startswith("https://") or not (host.endswith(".googleusercontent.com") or host.endswith(".google.com")):
            raise PickerError("Нет миниатюры", "No thumbnail", code="no_thumb", status=404)
        link = re.sub(r"=s\d+$", "=s220", link)
        response, data = self._request("GET", link, stream_limit=MAX_THUMB)
        mime = str(response.headers.get("content-type") or "image/jpeg").split(";")[0]
        if not mime.startswith("image/"):
            raise PickerError("Нет миниатюры", "No thumbnail", code="no_thumb", status=404)
        with self._lock:
            self._thumbs[file_id] = (mime, data)
            while len(self._thumbs) > 300:
                self._thumbs.popitem(last=False)
        return mime, data


# --------------------------------------------------------------------------
# HTTP routes
# --------------------------------------------------------------------------

def _lang() -> str:
    try:
        from .pgdata import load_preferences
        return "en" if load_preferences().get("language") == "en" else "ru"
    except Exception:
        return "ru"


def register(app, *, transport: Optional[httpx.BaseTransport] = None) -> Picker:
    import asyncio
    from fastapi.responses import JSONResponse, Response

    picker = Picker(lambda: getattr(app.state, "sync_service", None), transport=transport)
    app.state.gdrive_picker = picker

    def fail(exc: BaseException) -> JSONResponse:
        if isinstance(exc, PickerError):
            ru, en, code, status = exc.ru, exc.en, exc.code, exc.status
        else:
            ru = getattr(exc, "ru", "") or str(exc) or type(exc).__name__
            en = getattr(exc, "en", "") or ru
            code = getattr(exc, "code", "") or "error"
            status = 401 if type(exc).__name__ == "DriveAuthError" else 400
            if type(exc).__name__ not in ("DriveError", "DriveAuthError"):
                log.warning("drive picker: %s", type(exc).__name__)
        return JSONResponse({"error": en if _lang() == "en" else ru, "error_ru": ru, "error_en": en, "code": code}, status)

    @app.get("/api/pg/gdrive/status")
    async def gdrive_status():
        try:
            return await asyncio.to_thread(picker.status)
        except Exception as exc:
            return fail(exc)

    @app.post("/api/pg/gdrive/authorize")
    async def gdrive_authorize():
        try:
            result = await asyncio.to_thread(picker.authorize)
            return {"ok": True, "auth_url": result.get("auth_url", ""), "state": result.get("state", "")}
        except Exception as exc:
            return fail(exc)

    @app.post("/api/pg/gdrive/authorize/cancel")
    async def gdrive_authorize_cancel():
        auth = picker._auth()
        if auth is not None:
            auth.cancel_flow()
        return {"ok": True}

    @app.get("/api/pg/gdrive/list")
    async def gdrive_list(view: str = "folder", folder: str = "root", q: str = "", kind: str = "all",
                          page: str = "", order: str = "", crumbs: int = 0):
        try:
            result = await asyncio.to_thread(picker.list, view=view, folder=folder, query=q, kind=kind,
                                             page_token=page, order=order)
            if crumbs and view == "folder" and folder != "root":
                result["path"] = await asyncio.to_thread(picker.folder_path, folder)
            return result
        except Exception as exc:
            return fail(exc)

    @app.get("/api/pg/gdrive/thumb")
    async def gdrive_thumb(id: str = ""):
        try:
            mime, data = await asyncio.to_thread(picker.thumbnail, id)
            return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})
        except Exception as exc:
            return fail(exc)

    @app.post("/api/pg/gdrive/download")
    async def gdrive_download(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}
        try:
            result = await asyncio.to_thread(picker.download, str(body.get("id") or ""), str(body.get("format") or ""))
        except Exception as exc:
            return fail(exc)
        return Response(result["data"], media_type=result["mime"], headers={
            "X-Memu-Name": urllib.parse.quote(result["name"]), "X-Memu-Note": result["note"],
            "X-Memu-Format": result["format"], "Cache-Control": "no-store"})

    return picker
