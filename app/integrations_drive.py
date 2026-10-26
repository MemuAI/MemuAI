"""Round 33: the rest of Google Drive for the assistant — folders, rename, move, copy, trash / restore / delete,
sharing, downloads and exports of Google Docs / Sheets / Slides.

User report: «в Google диск ИИ не может удалять файлы и переименовывать их».  Two reasons: there were no such tools,
and Round 30 asked Google only for ``drive.readonly`` + ``drive.file`` — with ``drive.file`` MemuAI may change only
the files it created itself, so renaming a file of the user would have failed with «not found» anyway.  Round 33
asks for ``drive`` (``google_account.SERVICES["drive"]``) and these tools check it up front
(:data:`SCOPES` → ``integrations.TOOL_SCOPES``) with a «Expand access» hint instead of a confusing 404.

The original five tools (list / read / create / upload / update) stay in :mod:`app.integrations`; it hands every
other ``drive_*`` name to :func:`run`.  HTTP and errors: :func:`app.integrations._google`.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote

G = "https://www.googleapis.com/auth/"
FULL = {G + "drive"}
READ = {G + "drive", G + "drive.readonly"}

TOOLS: List[str] = [
    "drive_get_file{file_id}",
    "drive_create_folder{name,folder_id}",
    "drive_rename{file_id,name}",
    "drive_move{file_id,folder_id}",
    "drive_copy{file_id,name,folder_id}",
    "drive_trash{file_id}",
    "drive_untrash{file_id}",
    "drive_delete_file{file_id}",
    "drive_share{file_id,email,role,type,notify,message}",
    "drive_list_permissions{file_id}",
    "drive_unshare{file_id,permission_id,email}",
    "drive_download{file_id,format,folder}",
    "drive_storage{}",
]
WRITE = {"drive_create_folder", "drive_rename", "drive_move", "drive_copy", "drive_trash", "drive_untrash",
         "drive_delete_file", "drive_share", "drive_unshare", "drive_download"}

ALWAYS = {"drive_delete_file"}
DANGER = {"drive_delete_file", "drive_trash", "drive_unshare"}

SCOPES: Dict[str, set] = {
    **{name: FULL for name in ("drive_rename", "drive_move", "drive_copy", "drive_trash", "drive_untrash",
                               "drive_delete_file", "drive_share", "drive_unshare", "drive_list_permissions")},
    "drive_download": READ,
    "drive_get_file": READ | {G + "drive.file"},
    "drive_create_folder": FULL | {G + "drive.file"},
}

HINT = ("Google Drive: ids come from drive_list_files. Rename with drive_rename, move with drive_move (folder_id "
        "'root' = My Drive), drive_trash is recoverable (drive_untrash), drive_delete_file is PERMANENT — use it only "
        "when the user explicitly asks to delete forever. drive_download saves a file (Google Docs/Sheets/Slides are "
        "exported: format pdf|docx|xlsx|pptx|txt|csv|md|html|odt|epub) to a folder on this computer.")

EXPORT = {
    "pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "txt": "text/plain", "csv": "text/csv", "md": "text/markdown", "html": "text/html", "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet", "odp": "application/vnd.oasis.opendocument.presentation",
    "epub": "application/epub+zip", "rtf": "application/rtf", "png": "image/png", "jpg": "image/jpeg", "svg": "image/svg+xml",
}
DEFAULT_EXPORT = {"application/vnd.google-apps.document": "docx", "application/vnd.google-apps.spreadsheet": "xlsx",
                  "application/vnd.google-apps.presentation": "pptx", "application/vnd.google-apps.drawing": "png"}
FOLDER = "application/vnd.google-apps.folder"
MAX_SAVE = 512 * 1024 * 1024
FIELDS = "id,name,mimeType,size,modifiedTime,createdTime,parents,webViewLink,trashed,shared,starred,owners(displayName,emailAddress),description"

def _ig():
    from . import integrations
    return integrations

def _drive(method: str, path: str, **kwargs: Any) -> Any:
    ig = _ig()
    return ig._google("google_drive", method, ig.DRIVE_API + path, **kwargs)

def _arg(text: str):
    return _ig().IntegrationArgumentError(text)

def _file_id(args: Dict[str, Any], key: str = "file_id") -> str:
    raw = str(args.get(key) or args.get("id") or "").strip()

    found = re.search(r"/(?:d|folders)/([A-Za-z0-9_-]{10,})", raw) or re.search(r"[?&]id=([A-Za-z0-9_-]{10,})", raw)
    if found:
        raw = found.group(1)
    if not raw:
        raise _arg("Укажите %s (его даёт drive_list_files)" % key)
    return raw

def _ref(file_id: str) -> str:
    return quote(file_id, safe="")

def _row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": row.get("id"), "name": row.get("name"), "mime": row.get("mimeType"), "size": row.get("size"),
            "folder": row.get("mimeType") == FOLDER, "parents": row.get("parents"), "link": row.get("webViewLink"),
            "modified": row.get("modifiedTime"), "trashed": row.get("trashed"), "shared": row.get("shared"),
            "owners": [o.get("emailAddress") or o.get("displayName") for o in row.get("owners") or [] if isinstance(o, dict)]}

def _common() -> Dict[str, str]:
    return {"supportsAllDrives": "true"}

def run(tool: str, args: Dict[str, Any]) -> Any:  # noqa: C901 - one flat dispatch table
    if tool == "drive_get_file":
        row = _drive("GET", "/files/" + _ref(_file_id(args)), params={"fields": FIELDS, **_common()})
        return {**_row(row), "description": row.get("description")}
    if tool == "drive_create_folder":
        name = str(args.get("name") or "").strip()
        if not name:
            raise _arg("Укажите name папки")
        body: Dict[str, Any] = {"name": name, "mimeType": FOLDER}
        if args.get("folder_id"):
            body["parents"] = [_file_id(args, "folder_id")]
        row = _drive("POST", "/files", params={"fields": "id,name,webViewLink", **_common()}, json_body=body)
        return {"created": True, "id": row.get("id"), "name": row.get("name"), "link": row.get("webViewLink")}
    if tool == "drive_rename":
        file_id = _file_id(args)
        name = str(args.get("name") or args.get("new_name") or "").strip()
        if not name:
            raise _arg("Укажите name — новое имя")
        row = _drive("PATCH", "/files/" + _ref(file_id), params={"fields": "id,name,webViewLink", **_common()},
                     json_body={"name": name})
        return {"renamed": True, "id": row.get("id"), "name": row.get("name"), "link": row.get("webViewLink")}
    if tool == "drive_move":
        file_id = _file_id(args)
        target = str(args.get("folder_id") or "").strip()
        if not target:
            raise _arg("Укажите folder_id — папку назначения ('root' — «Мой диск»)")
        target = "root" if target.lower() in ("root", "my drive", "мой диск") else _file_id(args, "folder_id")
        current = _drive("GET", "/files/" + _ref(file_id), params={"fields": "parents", **_common()})
        params = {"addParents": target, "fields": "id,name,parents,webViewLink", **_common()}
        old = ",".join(p for p in current.get("parents") or [] if p != target)
        if old:
            params["removeParents"] = old
        row = _drive("PATCH", "/files/" + _ref(file_id), params=params, json_body={})
        return {"moved": True, "id": row.get("id"), "name": row.get("name"), "parents": row.get("parents")}
    if tool == "drive_copy":
        file_id = _file_id(args)
        body = {}
        if str(args.get("name") or "").strip():
            body["name"] = str(args["name"]).strip()
        if args.get("folder_id"):
            body["parents"] = [_file_id(args, "folder_id")]
        row = _drive("POST", "/files/%s/copy" % _ref(file_id), params={"fields": "id,name,webViewLink", **_common()}, json_body=body)
        return {"copied": True, "id": row.get("id"), "name": row.get("name"), "link": row.get("webViewLink")}
    if tool in ("drive_trash", "drive_untrash"):
        file_id = _file_id(args)
        row = _drive("PATCH", "/files/" + _ref(file_id), params={"fields": "id,name,trashed", **_common()},
                     json_body={"trashed": tool == "drive_trash"})
        return {"id": row.get("id"), "name": row.get("name"), "trashed": bool(row.get("trashed")),
                "note": "Файл в корзине Диска 30 дней — drive_untrash вернёт его." if tool == "drive_trash" else ""}
    if tool == "drive_delete_file":
        file_id = _file_id(args)
        _drive("DELETE", "/files/" + _ref(file_id), params=_common())
        return {"id": file_id, "deleted_forever": True}
    if tool == "drive_share":
        file_id = _file_id(args)
        kind = str(args.get("type") or "").strip().lower() or ("user" if args.get("email") else "anyone")
        if kind not in ("user", "group", "domain", "anyone"):
            raise _arg("type: user, group, domain или anyone (по ссылке)")
        role = str(args.get("role") or "reader").strip().lower()
        role = {"viewer": "reader", "editor": "writer", "comment": "commenter", "читатель": "reader", "редактор": "writer"}.get(role, role)
        if role not in ("reader", "commenter", "writer", "owner"):
            raise _arg("role: reader, commenter или writer (owner — передать владение)")
        body = {"type": kind, "role": role}
        params: Dict[str, Any] = {"fields": "id,role,type,emailAddress", **_common()}
        if kind in ("user", "group"):
            email = str(args.get("email") or "").strip()
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                raise _arg("Укажите email — с кем поделиться")
            body["emailAddress"] = email
            notify = str(args.get("notify") if args.get("notify") is not None else "true").lower() not in ("0", "false", "no")
            params["sendNotificationEmail"] = "true" if notify else "false"
            if notify and str(args.get("message") or "").strip():
                params["emailMessage"] = str(args["message"])[:2000]
        elif kind == "domain":
            body["domain"] = str(args.get("email") or args.get("domain") or "").split("@")[-1].strip()
            if not body["domain"]:
                raise _arg("Для type=domain укажите domain")
        if role == "owner":
            params["transferOwnership"] = "true"
        row = _drive("POST", "/files/%s/permissions" % _ref(file_id), params=params, json_body=body)
        link = _drive("GET", "/files/" + _ref(file_id), params={"fields": "webViewLink", **_common()}).get("webViewLink")
        return {"shared": True, "permission_id": row.get("id"), "role": row.get("role"), "type": row.get("type"),
                "email": row.get("emailAddress"), "link": link}
    if tool == "drive_list_permissions":
        rows = _drive("GET", "/files/%s/permissions" % _ref(_file_id(args)),
                      params={"fields": "permissions(id,role,type,emailAddress,displayName,domain)", **_common()})
        return {"permissions": rows.get("permissions") or []}
    if tool == "drive_unshare":
        file_id = _file_id(args)
        permission = str(args.get("permission_id") or "").strip()
        if not permission:
            email = str(args.get("email") or "").strip().lower()
            if not email:
                raise _arg("Укажите permission_id или email (drive_list_permissions)")
            rows = _drive("GET", "/files/%s/permissions" % _ref(file_id),
                          params={"fields": "permissions(id,emailAddress,type)", **_common()}).get("permissions") or []
            match = next((r for r in rows if str(r.get("emailAddress") or "").lower() == email
                          or (email in ("anyone", "link", "по ссылке") and r.get("type") == "anyone")), None)
            if not match:
                raise _arg("У файла нет доступа для %s" % email)
            permission = str(match["id"])
        _drive("DELETE", "/files/%s/permissions/%s" % (_ref(file_id), quote(permission, safe="")), params=_common())
        return {"unshared": True, "permission_id": permission}
    if tool == "drive_download":
        return _download(args)
    if tool == "drive_storage":
        about = _drive("GET", "/about", params={"fields": "user(emailAddress),storageQuota"})
        quota = about.get("storageQuota") or {}

        def gb(value: Any) -> Any:
            try:
                return round(int(value) / (1024 ** 3), 2)
            except (TypeError, ValueError):
                return None
        return {"account": (about.get("user") or {}).get("emailAddress"), "limit_gb": gb(quota.get("limit")),
                "used_gb": gb(quota.get("usage")), "drive_gb": gb(quota.get("usageInDrive")),
                "trash_gb": gb(quota.get("usageInDriveTrash"))}
    raise _arg("Неизвестный инструмент Google Drive: %s" % tool)

def _download(args: Dict[str, Any]) -> Dict[str, Any]:
    """A Drive file → a folder on this computer (Google formats exported to ``format``)."""
    import httpx
    ig = _ig()
    file_id = _file_id(args)
    meta = _drive("GET", "/files/" + _ref(file_id), params={"fields": "id,name,mimeType,size", **_common()})
    mime = str(meta.get("mimeType") or "")
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", str(meta.get("name") or "file")).strip(" .") or "file"
    if mime == FOLDER:
        raise _arg("Это папка — скачать можно только файлы")
    fmt = str(args.get("format") or "").strip().lower().lstrip(".")
    if mime.startswith("application/vnd.google-apps"):
        fmt = fmt or DEFAULT_EXPORT.get(mime, "pdf")
        if fmt not in EXPORT:
            raise _arg("format: " + ", ".join(sorted(EXPORT)))
        url, params = ig.DRIVE_API + "/files/%s/export" % _ref(file_id), {"mimeType": EXPORT[fmt]}
        if not name.lower().endswith("." + fmt):
            name += "." + fmt
    else:
        if fmt and not name.lower().endswith("." + fmt):
            raise _arg("Конвертировать можно только файлы Google (Docs, Sheets, Slides); этот скачивается как есть")
        url, params = ig.DRIVE_API + "/files/" + _ref(file_id), {"alt": "media", **_common()}
    folder_raw = str(args.get("folder") or "").strip().strip('"')
    folder = Path(os.path.expandvars(os.path.expanduser(folder_raw))) if folder_raw else Path.home() / "Downloads" / "MemuAI"
    if not folder.is_absolute():
        raise _arg("folder — полный путь к папке на этом компьютере")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    stem, suffix, n = target.stem, target.suffix, 1
    while target.exists():
        n += 1
        target = folder / ("%s (%d)%s" % (stem, n, suffix))
    token = ig._google_access_token("google_drive")
    size = 0
    try:
        with ig._client() as client:
            with client.stream("GET", url, headers={"Authorization": "Bearer " + token}, params=params) as response:
                if response.status_code >= 400:
                    response.read()
                    raise ig._google_error("google_drive", response)
                tmp = target.with_name(target.name + ".part")
                with open(tmp, "wb") as out:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_SAVE:
                            out.close()
                            tmp.unlink(missing_ok=True)
                            raise _arg("Файл больше 512 МБ — скачайте его из браузера")
                        out.write(chunk)
                tmp.replace(target)
    except httpx.HTTPError as exc:
        raise ig._network_error("Google Drive", exc) from exc
    return {"saved": True, "path": str(target), "size": size, "name": target.name}
