"""Интеграции MemuAI: GitHub, Google (Gmail, Calendar, Drive) и Notion.

Модуль решает три задачи:

1. Хранит ключи доступа локально (``%APPDATA%/MemuAI/memuai/integrations.json``,
   файл создаётся с правами 0600 и никогда не отдаётся в интерфейс целиком).
   Round 30: Google — один аккаунт на всё (:mod:`app.google_account`: вход по
   ссылке, loopback + PKCE, refresh token в защищённом хранилище, выбор
   сервисов); Notion — :mod:`app.integrations_notion` (токен интеграции или
   вход через Notion).
2. Выдаёт access_token (Google — через общий аккаунт, кэш в памяти).
3. Выполняет инструменты (``run_tool``), которые ИИ вызывает через скрытый
   протокол memuai-tool: репозитории, файлы, issues, почта Gmail, файлы
   Google Drive, события календаря, страницы и базы Notion.

Все функции синхронные: из FastAPI их нужно вызывать через ``asyncio.to_thread``.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import httpx

try:
    from .pgdata import MEMU_DIR
except ImportError:  # pragma: no cover - запуск файла напрямую
    from app.pgdata import MEMU_DIR  # type: ignore

from . import integrations_drive, integrations_gcal, integrations_github, integrations_gmail  # noqa: E402
from . import integrations_gworkspace, integrations_notion_tools  # noqa: E402

def integrations_gmail_scopes() -> Dict[str, set]:
    """Gmail tools beyond reading and sending need gmail.modify / mail.google.com / gmail.settings.basic."""
    g = "https://www.googleapis.com/auth/"
    mail, modify, settings = "https://mail.google.com/", g + "gmail.modify", g + "gmail.settings.basic"
    out: Dict[str, set] = {}
    for name in ("gmail_archive", "gmail_mark_read", "gmail_star", "gmail_modify", "gmail_trash", "gmail_untrash",
                 "gmail_bulk", "gmail_create_label", "gmail_update_label", "gmail_delete_label"):
        out[name] = {mail, modify}
    out["gmail_delete"] = {mail}
    for name in ("gmail_create_filter", "gmail_delete_filter", "gmail_vacation"):
        out[name] = {settings}
    out["gmail_list_filters"] = {settings, mail, modify, g + "gmail.readonly"}
    return out

STORE_NAME = "integrations.json"
TIMEOUT = 25.0
MAX_TEXT = 100000

MAX_DOWNLOAD = 4 * MAX_TEXT

RETRY_STATUSES = {429, 502, 503, 504}
RETRY_DELAY = 0.8
GITHUB_API = "https://api.github.com"
DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"

SERVICES: Dict[str, Dict[str, Any]] = {
    "github": {
        "title": "GitHub",
        "required": ["token"],
        "optional": ["default_repo"],
        "secret": ["token"],
    },

    "gmail": {"title": "Gmail", "kind": "google", "google": "gmail", "required": [], "optional": [], "secret": []},
    "google_calendar": {"title": "Google Calendar", "kind": "google", "google": "calendar",
                        "required": [], "optional": [], "secret": []},
    "google_drive": {"title": "Google Drive", "kind": "google", "google": "drive", "required": [], "optional": [], "secret": []},

    "google_docs": {"title": "Google Docs", "kind": "google", "google": "docs", "required": [], "optional": [], "secret": []},
    "google_sheets": {"title": "Google Sheets", "kind": "google", "google": "sheets", "required": [], "optional": [], "secret": []},
    "google_tasks": {"title": "Google Tasks", "kind": "google", "google": "tasks", "required": [], "optional": [], "secret": []},
    "google_contacts": {"title": "Google Contacts", "kind": "google", "google": "contacts", "required": [], "optional": [], "secret": []},

    "notion": {"title": "Notion", "kind": "notion", "required": [], "optional": [], "secret": []},
}

TOOLS: Dict[str, List[str]] = {
    "github": [
        "github_status{}",
        "github_list_repos{query,limit}",
        "github_repo_info{repo}",
        "github_list_files{repo,path,ref}",
        "github_read_file{repo,path,ref}",
        "github_search_code{query,repo}",
        "github_list_commits{repo,limit,branch}",
        "github_list_issues{repo,state,limit}",
        "github_create_issue{repo,title,body,labels,assignees}",
        "github_write_file{repo,path,content,message,branch,content_base64}",
        *integrations_github.TOOLS,
    ],
    "gmail": list(integrations_gmail.TOOLS),
    "google_drive": [
        "drive_list_files{query,limit,folder_id,search_content,mime,trashed,shared_with_me,page_token}",
        "drive_read_file{file_id}",
        "drive_create_file{name,content,mime,folder_id}",
        "drive_upload_file{path,name,folder_id}",
        "drive_update_file{file_id,content,mime}",
        *integrations_drive.TOOLS,
    ],
    "google_calendar": [
        "calendar_list_calendars{}",
        "calendar_list_events{calendar_id,days,query,limit,time_min,time_max}",
        "calendar_create_event{summary,start,end,description,location,calendar_id,timezone,attendees,recurrence,reminders,meet,send_updates}",
        "calendar_update_event{event_id,summary,start,end,description,location,calendar_id,timezone,attendees,recurrence,reminders,meet,send_updates}",
        "calendar_delete_event{event_id,calendar_id,send_updates}",
        *integrations_gcal.TOOLS,
    ],
    "google_docs": list(integrations_gworkspace.DOCS_TOOLS),
    "google_sheets": list(integrations_gworkspace.SHEETS_TOOLS),
    "google_tasks": list(integrations_gworkspace.TASKS_TOOLS),
    "google_contacts": list(integrations_gworkspace.CONTACTS_TOOLS),
    "notion": [
        "notion_search{query,type,limit}",
        "notion_read_page{page_id}",
        "notion_query_database{database_id,filter,sorts,limit}",
        "notion_create_page{parent_id,title,content,parent_type,properties}",
        "notion_append{page_id,content}",
        *integrations_notion_tools.TOOLS,
    ],
}

WRITE_TOOLS = {"github_write_file", "github_create_issue", "drive_create_file", "drive_upload_file",
               "drive_update_file", "calendar_create_event", "calendar_update_event", "calendar_delete_event",
               "notion_create_page", "notion_append",
               *integrations_github.WRITE, *integrations_gmail.WRITE, *integrations_drive.WRITE,
               *integrations_gcal.WRITE, *integrations_gworkspace.WRITE, *integrations_notion_tools.WRITE}

ALWAYS_CONFIRM = {"gmail_send", *integrations_gmail.ALWAYS, *integrations_github.ALWAYS, *integrations_drive.ALWAYS,
                  *integrations_gcal.ALWAYS, *integrations_gworkspace.ALWAYS}

DANGER_TOOLS = {"calendar_delete_event", "drive_update_file", *integrations_github.DANGER, *integrations_gmail.DANGER,
                *integrations_drive.DANGER, *integrations_gcal.DANGER, *integrations_gworkspace.DANGER,
                *integrations_notion_tools.DANGER}

from .telegram_use import WRITE as _TELEGRAM_USE_WRITE, DANGER as _TELEGRAM_USE_DANGER
from .browser_extension import WRITE as _CHROME_WRITE
from .telegram_use import TOOLS as _TELEGRAM_USE_TOOLS
from .browser_extension import TOOLS as _CHROME_TOOLS
TOOLS["telegram_use"] = list(_TELEGRAM_USE_TOOLS)
TOOLS["chrome_extension"] = list(_CHROME_TOOLS)
WRITE_TOOLS.update(_TELEGRAM_USE_WRITE | _CHROME_WRITE)
ALWAYS_CONFIRM.update(_TELEGRAM_USE_WRITE)
DANGER_TOOLS.update(_TELEGRAM_USE_DANGER | {"chrome_close"})

HINTS = {"github": integrations_github.HINT, "gmail": integrations_gmail.HINT, "google_drive": integrations_drive.HINT,
         "google_calendar": integrations_gcal.HINT, "google_docs": integrations_gworkspace.DOCS_HINT,
         "google_sheets": integrations_gworkspace.SHEETS_HINT, "google_tasks": integrations_gworkspace.TASKS_HINT,
         "google_contacts": integrations_gworkspace.CONTACTS_HINT, "notion": integrations_notion_tools.HINT}

TOOL_SCOPES: Dict[str, set] = {**integrations_gmail_scopes(), **integrations_drive.SCOPES, **integrations_gcal.SCOPES}

MAX_UPLOAD = 25 * 1024 * 1024

_lock = threading.Lock()
_tokens: Dict[str, Dict[str, Any]] = {}

class IntegrationError(RuntimeError):
    """Ошибка интеграции с человеческим текстом для интерфейса."""

class IntegrationArgumentError(IntegrationError):
    """The model passed a bad argument: an answer for the model, not a sign of a broken connection."""

class IntegrationAccountError(IntegrationError):
    """Round 30: not signed in / service switched off / consent missing — Settings says so already, not a «last error»."""

def _client() -> httpx.Client:
    """Separate function so tests can swap the transport (httpx.MockTransport)."""
    return httpx.Client(timeout=httpx.Timeout(TIMEOUT, connect=10.0), follow_redirects=True)

def _sleep(seconds: float) -> None:
    time.sleep(seconds)

def _int(value: Any, default: int, low: int, high: int) -> int:
    """A number from the model's arguments: "5", 5.0, "abc" and None never raise."""
    try:
        number = int(float(str(value).strip())) if value not in (None, "") else default
    except (TypeError, ValueError):
        number = default
    return max(low, min(number, high))

def _network_error(title: str, exc: Exception) -> IntegrationError:
    if isinstance(exc, httpx.TimeoutException):
        return IntegrationError("%s не ответил за %d с — сервис перегружен или нет сети. Попробуйте ещё раз."
                                % (title, int(TIMEOUT)))
    if isinstance(exc, httpx.ConnectError):
        return IntegrationError("Нет соединения с %s: проверьте интернет, прокси или VPN (%s)."
                                % (title, _short(str(exc)) or type(exc).__name__))
    return IntegrationError("Сетевая ошибка %s: %s" % (title, _short(str(exc)) or type(exc).__name__))

def _send(title: str, method: str, url: str, **kwargs: Any) -> httpx.Response:
    """One request; reads (GET) are retried once after a network error or 429/502/503/504."""
    attempts = 2 if method.upper() == "GET" else 1
    for attempt in range(attempts):
        try:
            with _client() as client:
                response = client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            if attempt + 1 < attempts and isinstance(exc, (httpx.TimeoutException, httpx.ConnectError,
                                                           httpx.RemoteProtocolError)):
                _sleep(RETRY_DELAY)
                continue
            raise _network_error(title, exc) from exc
        if attempt + 1 < attempts and response.status_code in RETRY_STATUSES:
            wait = _retry_after(response)
            if wait is not None and wait <= 5:
                _sleep(max(wait, RETRY_DELAY))
                continue
        return response
    return response  # pragma: no cover - the loop always returns

def _retry_after(response: httpx.Response) -> Optional[float]:
    value = response.headers.get("retry-after")
    if value is None:
        return RETRY_DELAY if response.status_code != 429 else None
    try:
        return float(value)
    except ValueError:
        return None

def _stream_text(title: str, url: str, headers: Dict[str, str], params: Optional[Dict[str, Any]] = None,
                 on_error=None) -> Tuple[bytes, bool, str]:
    """Download at most MAX_DOWNLOAD bytes: (data, cut, content-type)."""
    try:
        with _client() as client:
            with client.stream("GET", url, headers=headers, params=params) as response:
                if response.status_code >= 400:
                    response.read()
                    if on_error is not None:
                        on_error(response)
                    raise IntegrationError("%s %s" % (title, response.status_code))
                chunks: List[bytes] = []
                size = 0
                cut = False
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= MAX_DOWNLOAD:
                        cut = True
                        break
                return b"".join(chunks)[:MAX_DOWNLOAD], cut, response.headers.get("content-type", "")
    except httpx.HTTPError as exc:
        raise _network_error(title, exc) from exc

def _decode(data: bytes) -> Tuple[str, bool]:
    """Text of a downloaded file, or ("", True) for a binary one (NUL bytes in the head)."""
    head = data[:8192]
    if b"\x00" in head and not (head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff")):
        return "", True
    for bom, codec in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if data.startswith(bom):
            return data.decode(codec, "replace"), False
    return data.decode("utf-8", "replace"), False

def _error_payload(response: httpx.Response) -> Dict[str, Any]:
    try:
        data = response.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}

def _store_path() -> Path:
    MEMU_DIR.mkdir(parents=True, exist_ok=True)
    return MEMU_DIR / STORE_NAME

def _load_store() -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if isinstance(value, dict)}

def _save_store(store: Dict[str, Dict[str, Any]]) -> None:
    path = _store_path()

    from .storage import save_json
    save_json(path, store, backup=False)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass

def _service_meta(service: str) -> Dict[str, Any]:
    meta = SERVICES.get(service)
    if not meta:
        raise IntegrationError("Неизвестная интеграция: %s" % service)
    return meta

def _missing(service: str, data: Dict[str, Any]) -> List[str]:
    meta = _service_meta(service)
    return [field for field in meta["required"] if not str(data.get(field) or "").strip()]

def _public_account(service: str, google: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Round 30: a Google service or Notion — the state comes from the shared account, not from typed fields."""
    meta = _service_meta(service)
    data = _load_store().get(service) or {}
    last_error = str(data.get("last_error") or "")
    if meta["kind"] == "google":
        row = next((item for item in (google or {}).get("services", []) if item["id"] == meta["google"]), {})
        connected = bool(row.get("ready"))
        account = ((google or {}).get("account") or {}).get("email", "") if connected else ""
        state = "off" if not connected else ("error" if last_error else "ok")
        extra = {"enabled": bool(row.get("enabled")), "granted": bool(row.get("granted"))}
    else:
        from . import integrations_notion
        notion = integrations_notion.status()
        connected = notion["connected"]
        account = notion["account"] if connected else ""
        last_error = notion["last_error"] if connected else ""
        state = "off" if not connected else ("error" if last_error else ("ok" if notion["checked"] else "unverified"))
        extra = {"mode": notion["mode"]}
    tools = list(TOOLS.get(service, []))
    return {"service": service, "title": meta["title"], "kind": meta["kind"], "connected": connected, "state": state,
            "checked": data.get("checked") or 0, "account": account, "missing": [], "updated": data.get("updated") or 0,
            "last_error": last_error if connected else "", "required": [], "optional": [], "values": {},
            "tools": tools, "write_tools": [t.split("{")[0] for t in tools if t.split("{")[0] in WRITE_TOOLS], **extra}

def _public(service: str, data: Dict[str, Any]) -> Dict[str, Any]:
    meta = _service_meta(service)
    missing = _missing(service, data)
    filled = {}
    for field in meta["required"] + meta["optional"]:
        value = str(data.get(field) or "")
        if field in meta["secret"]:
            filled[field] = ("•" * 8 + value[-4:]) if value else ""
        else:
            filled[field] = value
    last_error = str(data.get("last_error") or "")

    if missing:
        state = "off"
    elif last_error:
        state = "error"
    elif data.get("checked"):
        state = "ok"
    else:
        state = "unverified"
    return {
        "service": service,
        "title": meta["title"],
        "connected": not missing,
        "state": state,
        "checked": data.get("checked") or 0,
        "account": str(data.get("account") or ""),
        "missing": missing,
        "updated": data.get("updated") or 0,
        "last_error": last_error,
        "required": list(meta["required"]),
        "optional": list(meta["optional"]),
        "values": filled,
        "tools": list(TOOLS.get(service, [])),
    }

GOOGLE_CHECKS = {
    "gmail": ("GET", "https://gmail.googleapis.com/gmail/v1/users/me/profile", None),
    "google_calendar": ("GET", CALENDAR_API + "/users/me/calendarList", {"maxResults": 1}),
    "google_drive": ("GET", DRIVE_API + "/about", {"fields": "user(emailAddress)"}),
    "google_docs": ("GET", "https://docs.googleapis.com/v1/documents/memuai-api-check-000000000", None),
    "google_sheets": ("GET", "https://sheets.googleapis.com/v4/spreadsheets/memuai-api-check-000000000", {"fields": "spreadsheetId"}),
    "google_tasks": ("GET", "https://tasks.googleapis.com/tasks/v1/users/@me/lists", {"maxResults": 1}),
    "google_contacts": ("GET", "https://people.googleapis.com/v1/people/me/connections", {"pageSize": 1, "personFields": "names"}),
}

def google_check_one(service: str) -> Dict[str, Any]:
    """{"service", "ok", "state": ok|disabled|scope|error, "error", "api", "enable_url"} — never raises."""
    name, api = _google_api(service)
    out: Dict[str, Any] = {"service": service, "api": api, "api_title": name, "ok": False, "state": "error", "error": ""}
    method, url, params = GOOGLE_CHECKS[service]
    try:
        _google(service, method, url, params=params, allow=(400, 404))
        out.update(ok=True, state="ok")
    except IntegrationAccountError as exc:
        out.update(state="account", error=str(exc))
    except IntegrationError as exc:
        text = str(exc)
        out["error"] = text
        if service in ("google_docs", "google_sheets") and "(403" in text:
            out.update(ok=True, state="ok", error="")
        elif "выключен в вашем проекте" in text:
            found = re.search(r"https://\S+", text)
            out.update(state="disabled", enable_url=found.group(0) if found else "")
        elif "scope" in text or "Расширить доступ" in text:
            out["state"] = "scope"
    except Exception as exc:  # pragma: no cover - a check must never break Settings
        out["error"] = type(exc).__name__
    return out

def google_check() -> Dict[str, Any]:
    """Check every ready Google service; services that are off are reported as such (no request)."""
    from . import google_account
    rows = []
    state = google_account.status()
    for row in state.get("services") or []:
        service = row["integration"]
        if not row.get("ready"):
            rows.append({"service": service, "id": row["id"], "ok": False, "state": "off", "api": row.get("api"),
                         "api_title": row.get("api_title"), "enable_url": row.get("api_url")})
            continue
        result = google_check_one(service)
        result["id"] = row["id"]
        if result["state"] == "ok":
            _remember(service)
        elif result["state"] in ("disabled", "error"):
            _remember(service, error=result["error"])
        rows.append(result)
    disabled = [r["api"] for r in rows if r["state"] == "disabled" and r.get("api")]
    return {"services": rows, "enable_all_url": google_account.enable_url(tuple(disabled)) if disabled else "",
            "checked": int(time.time())}

def status() -> Dict[str, Any]:
    """Состояние всех интеграций без секретов."""
    store = _load_store()
    try:
        from . import google_account
        google = google_account.status()
    except Exception:
        google = {}
    rows = []
    for name, meta in SERVICES.items():
        if meta.get("kind"):
            try:
                rows.append(_public_account(name, google))
            except Exception:
                continue
        else:
            rows.append(_public(name, store.get(name) or {}))
    for row in rows:
        if row.get("service") in HINTS:
            row["hint"] = HINTS[row["service"]]
    from . import connected_services
    rows.extend(connected_services.status_rows())
    return {"services": rows, "always_confirm": sorted(ALWAYS_CONFIRM | connected_services.always_confirm()),
            "write_tools": sorted(WRITE_TOOLS | connected_services.write_tools()),
            "danger_tools": sorted(DANGER_TOOLS | connected_services.danger_tools())}

def _store_transaction():
    """Thread lock plus the shared cross-process JSON lock.

    MemuAI and the Proxy both read-modify-write this file; the thread lock
    alone let one process overwrite credentials the other had just saved.
    """
    from contextlib import ExitStack
    from .storage import _json_transaction
    stack = ExitStack()
    stack.enter_context(_lock)
    stack.enter_context(_json_transaction(_store_path()))
    return stack

def save_credentials(service: str, values: Dict[str, Any]) -> Dict[str, Any]:
    meta = _service_meta(service)
    if meta.get("kind"):
        raise IntegrationError("%s подключается кнопкой входа в Настройки → Интеграции" % meta["title"])
    allowed = set(meta["required"]) | set(meta["optional"])
    with _store_transaction():
        store = _load_store()
        current = dict(store.get(service) or {})
        for key, value in (values or {}).items():
            if key not in allowed:
                continue
            text = _clean_value(key, value)

            if not text and key in meta["secret"]:
                continue
            if key in meta["secret"] and text.startswith("\u2022"):
                continue
            current[key] = text
        current["updated"] = int(time.time())
        current.pop("last_error", None)
        current.pop("checked", None)
        store[service] = current
        _save_store(store)
    _tokens.pop(service, None)
    return _public(service, current)

def _clean_value(key: str, value: Any) -> str:
    """Pasted values: spaces and line breaks around, quotes, "Bearer "/"token " before a GitHub token."""
    text = str(value or "").strip().strip('"').strip("'").strip()
    if key == "token":
        text = re.sub(r"^(?:authorization:\s*)?(?:bearer|token)\s+", "", text, flags=re.I)
    if key in ("token", "refresh_token", "client_secret", "client_id"):
        text = re.sub(r"\s+", "", text)
    if key == "default_repo" and text:
        try:
            text = _parse_repo(text)
        except IntegrationError:
            pass
    return text

def disconnect(service: str) -> Dict[str, Any]:
    meta = _service_meta(service)
    if meta.get("kind") == "google":

        from . import google_account
        google_account.set_enabled(meta["google"], False)
        _tokens.pop(service, None)
        return _public_account(service, google_account.status())
    if meta.get("kind") == "notion":
        from . import integrations_notion
        integrations_notion.disconnect()
        return _public_account(service)
    with _store_transaction():
        store = _load_store()
        store.pop(service, None)
        _save_store(store)
    _tokens.pop(service, None)
    return _public(service, {})

def _credentials(service: str) -> Dict[str, Any]:
    data = _load_store().get(service) or {}
    missing = _missing(service, data)
    if missing:
        raise IntegrationError(
            "%s не подключён. Откройте Настройки → Интеграции и заполните: %s"
            % (_service_meta(service)["title"], ", ".join(missing))
        )
    return data

def _remember(service: str, account: str = "", error: str = "") -> None:
    with _store_transaction():
        store = _load_store()
        current = dict(store.get(service) or {})
        if not current and not SERVICES.get(service, {}).get("kind"):
            return
        if account:
            current["account"] = account
        if error:
            current["last_error"] = error[:400]
        else:
            current.pop("last_error", None)
            current["checked"] = int(time.time())
        store[service] = current
        _save_store(store)

def _clear_error_after_success(service: str) -> None:
    """A tool call that worked clears a stale "last error" (it used to stay in Settings forever)."""
    data = _load_store().get(service) or {}
    if data.get("last_error") or not data.get("checked"):
        _remember(service)

def _short(text: str) -> str:
    body = " ".join(str(text or "").split())
    return body[:300]

def _github_headers(token: str, accept: str = "application/vnd.github+json") -> Dict[str, str]:
    return {
        "Authorization": "Bearer " + token,
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "MemuAI",
    }

def _github_error(response: httpx.Response) -> IntegrationError:
    """GitHub's answer in words: bad token, missing scope, rate limit (403/429), not found, validation."""
    status = response.status_code
    data = _error_payload(response)
    message = str(data.get("message") or "").strip() or _short(response.text)
    remaining = response.headers.get("x-ratelimit-remaining")
    if status == 401:
        return IntegrationError("GitHub отклонил токен (401: %s). Перевыпустите Personal Access Token и сохраните "
                                "его в Настройки → Интеграции." % (message or "Bad credentials"))
    if status == 429 or (status == 403 and (remaining == "0" or "rate limit" in message.lower())):
        reset = response.headers.get("x-ratelimit-reset") or ""
        wait = ""
        try:
            seconds = max(0, int(float(reset)) - int(time.time()))
            wait = " Лимит обновится через %d мин." % max(1, round(seconds / 60))
        except ValueError:
            retry = response.headers.get("retry-after")
            wait = (" Повторите через %s с." % retry) if retry else ""
        return IntegrationError("GitHub: превышен лимит запросов (%s).%s" % (status, wait))
    if status == 403:

        need = _github_need(response)
        return IntegrationError("GitHub запретил действие (403: %s). %s" % (message or "Forbidden", need or (
            "Токену не хватает прав. Classic-токен: отметьте repo, workflow, gist, delete_repo. Fine-grained: "
            "Administration, Contents, Issues, Pull requests, Actions, Workflows — Read and write, у токена должен быть "
            "выбран этот репозиторий (или All repositories). Настройки → Интеграции → GitHub → «Какие права нужны».")))
    if status == 404:
        need = _github_need(response)
        return IntegrationError("GitHub: объект не найден или токену не хватает прав (404)." + (" " + need if need else
                                " Приватный репозиторий без доступа у токена GitHub тоже показывает как 404."))
    if status == 409:
        return IntegrationError("GitHub: конфликт (409: %s) — репозиторий пуст или файл изменился одновременно."
                                % message)
    if status == 422:
        errors = data.get("errors") if isinstance(data.get("errors"), list) else []
        details = "; ".join(str((e or {}).get("message") or (e or {}).get("code") or e) for e in errors[:3])
        return IntegrationError("GitHub не принял запрос (422: %s%s)." % (message, (" — " + details) if details else ""))
    if status >= 500:
        return IntegrationError("GitHub временно недоступен (%s). Попробуйте позже." % status)
    return IntegrationError("GitHub %s: %s" % (status, message))

def _github_need(response: httpx.Response) -> str:
    """«Нужно: …» from GitHub's headers (what the endpoint accepts vs. what the token has)."""
    fine = (response.headers.get("x-accepted-github-permissions") or "").strip()
    classic = (response.headers.get("x-accepted-oauth-scopes") or "").strip()
    has = response.headers.get("x-oauth-scopes")
    if fine:
        pretty = "; ".join(part.strip().replace("=", ": ") for part in fine.split(";") if part.strip())
        return "Fine-grained токену нужно разрешение: %s (Настройки → Интеграции → GitHub)." % pretty
    if classic:
        return "Classic-токену нужен scope: %s; у токена сейчас: %s." % (classic, (has or "").strip() or "—")
    return ""

def _github(method: str, path: str, *, params: Optional[Dict[str, Any]] = None,
            json_body: Optional[Dict[str, Any]] = None) -> Any:
    token = _credentials("github")["token"]
    url = path if path.startswith("http") else GITHUB_API + path
    response = _send("GitHub", method, url, headers=_github_headers(token), params=params, json=json_body)
    if response.status_code >= 400:
        raise _github_error(response)
    if not response.content:
        return {}
    try:
        return response.json()
    except Exception:
        return {"text": response.text[:MAX_TEXT]}

_REPO_PART = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")

def _parse_repo(value: str) -> str:
    """``owner/name`` from what people paste: a URL with /tree/branch/path, ``.git``, an SSH remote."""
    repo = str(value or "").strip()
    if repo.startswith("git@"):
        repo = repo.split(":", 1)[-1]
    elif "://" in repo or repo.lower().startswith(("github.com/", "www.github.com/")):
        parsed = urlparse(repo if "://" in repo else "https://" + repo)
        repo = parsed.path
    parts = [part for part in repo.strip("/").split("/") if part]
    if len(parts) > 2 and ("://" in str(value) or str(value).lower().startswith(("github.com", "www.github.com", "git@"))):
        parts = parts[:2]
    if len(parts) != 2:
        raise IntegrationArgumentError("Укажите репозиторий в виде owner/name")
    owner, name = parts[0], parts[1]
    if name.lower().endswith(".git"):
        name = name[:-4]
    if not _REPO_PART.match(owner) or not _REPO_PART.match(name) or owner.startswith(".") or name in (".", ".."):
        raise IntegrationArgumentError("Некорректное имя репозитория: %s" % _short(value))
    return "%s/%s" % (owner, name)

def _repo(args: Dict[str, Any]) -> str:
    repo = str(args.get("repo") or "").strip()
    if not repo:
        repo = str((_load_store().get("github") or {}).get("default_repo") or "").strip()
    return _parse_repo(repo)

def _raise_github(response: httpx.Response) -> None:
    raise _github_error(response)

def _github_file_text(repo: str, path: str, ref: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """Text of a file from a contents row; files over 1 MB come without content and are streamed raw."""
    raw = str(row.get("content") or "")
    size = int(row.get("size") or 0)
    if raw and row.get("encoding", "base64") == "base64":
        try:
            data = base64.b64decode(raw.encode("ascii"), validate=False)
        except Exception:
            data = b""
        cut = False
    elif size > 0 or row.get("encoding") == "none":
        token = _credentials("github")["token"]
        data, cut, _type = _stream_text(
            "GitHub", GITHUB_API + "/repos/%s/contents/%s" % (repo, quote(path, safe="/")),
            _github_headers(token, "application/vnd.github.raw"), params={"ref": ref} if ref else None,
            on_error=_raise_github)
    else:
        data, cut = b"", False
    text, binary = _decode(data)
    result: Dict[str, Any] = {"path": row.get("path") or path, "size": size, "sha": row.get("sha")}
    if binary:
        result.update({"binary": True, "text": "",
                       "note": "Двоичный файл (%d байт): прочитать его текстом нельзя." % size})
        return result
    result.update({"text": text[:MAX_TEXT], "truncated": cut or len(text) > MAX_TEXT})
    return result

def _github_tool(tool: str, args: Dict[str, Any]) -> Any:
    limit = _int(args.get("limit"), 20, 1, 100)
    if tool == "github_status":
        from .integrations_github import token_info
        info = token_info()
        _remember("github", account=str(info.get("login") or ""))
        return info
    if tool == "github_list_repos":
        query = str(args.get("query") or "").strip().lower()

        rows: List[Dict[str, Any]] = []
        for page in range(1, 11 if query else 2):
            chunk = _github("GET", "/user/repos", params={
                "per_page": 100 if query else limit, "page": page, "sort": "updated",
                "affiliation": "owner,collaborator,organization_member"})
            chunk = chunk if isinstance(chunk, list) else []
            rows += chunk
            matched = [row for row in rows if query in str(row.get("full_name") or "").lower()
                       or query in str(row.get("description") or "").lower()] if query else rows
            if len(matched) >= limit or len(chunk) < (100 if query else limit):
                break
        items = [
            {
                "full_name": row.get("full_name"),
                "private": row.get("private"),
                "default_branch": row.get("default_branch"),
                "description": row.get("description"),
                "updated_at": row.get("updated_at"),
                "language": row.get("language"),
            }
            for row in (rows or [])
            if not query or query in str(row.get("full_name") or "").lower() or query in str(row.get("description") or "").lower()
        ][:limit]
        return {"count": len(items), "repos": items}
    if tool == "github_repo_info":
        row = _github("GET", "/repos/" + _repo(args))
        return {
            "full_name": row.get("full_name"),
            "private": row.get("private"),
            "default_branch": row.get("default_branch"),
            "description": row.get("description"),
            "open_issues": row.get("open_issues_count"),
            "size_kb": row.get("size"),
            "pushed_at": row.get("pushed_at"),
        }
    if tool == "github_list_files":
        path = str(args.get("path") or "").strip().lstrip("/")
        params = {"ref": str(args.get("ref") or "")} if args.get("ref") else None
        rows = _github("GET", "/repos/%s/contents/%s" % (_repo(args), quote(path, safe="/")), params=params)
        if isinstance(rows, dict):
            rows = [rows]
        rows = rows if isinstance(rows, list) else []
        return {
            "path": path or "/",
            "entries": [{"name": row.get("name"), "type": row.get("type"), "size": row.get("size"), "path": row.get("path")} for row in rows[:300]],
            "total": len(rows),
            "truncated": len(rows) > 300,
        }
    if tool == "github_read_file":
        path = str(args.get("path") or "").strip().lstrip("/")
        if not path:
            raise IntegrationArgumentError("Укажите path файла")
        params = {"ref": str(args.get("ref") or "")} if args.get("ref") else None
        repo = _repo(args)
        row = _github("GET", "/repos/%s/contents/%s" % (repo, quote(path, safe="/")), params=params)
        if isinstance(row, list):
            raise IntegrationArgumentError("Это каталог, а не файл: используйте github_list_files")
        if row.get("type") and row.get("type") != "file":
            raise IntegrationArgumentError("Это не файл (%s): %s" % (row.get("type"), path))
        return _github_file_text(repo, path, str(args.get("ref") or ""), row)
    if tool == "github_search_code":
        query = str(args.get("query") or "").strip()
        if not query:
            raise IntegrationArgumentError("Укажите query для поиска")
        if args.get("repo"):
            query += " repo:" + _repo(args)
        rows = _github("GET", "/search/code", params={"q": query, "per_page": limit})
        return {
            "total": rows.get("total_count"),
            "items": [{"path": row.get("path"), "repo": (row.get("repository") or {}).get("full_name"), "url": row.get("html_url")} for row in (rows.get("items") or [])],
        }
    if tool == "github_list_commits":
        params: Dict[str, Any] = {"per_page": limit}
        if args.get("branch"):
            params["sha"] = str(args.get("branch"))
        rows = _github("GET", "/repos/%s/commits" % _repo(args), params=params)
        return {
            "commits": [
                {
                    "sha": str(row.get("sha") or "")[:10],
                    "message": _short((row.get("commit") or {}).get("message") or ""),
                    "author": ((row.get("commit") or {}).get("author") or {}).get("name"),
                    "date": ((row.get("commit") or {}).get("author") or {}).get("date"),
                }
                for row in (rows or [])
            ]
        }
    if tool == "github_list_issues":
        state = str(args.get("state") or "open").strip().lower()
        params = {"state": state if state in ("open", "closed", "all") else "open", "per_page": limit}
        rows = _github("GET", "/repos/%s/issues" % _repo(args), params=params)
        return {
            "issues": [
                {"number": row.get("number"), "title": row.get("title"), "state": row.get("state"), "user": (row.get("user") or {}).get("login"), "pull_request": bool(row.get("pull_request"))}
                for row in (rows or [])
            ]
        }
    if tool == "github_create_issue":
        title = str(args.get("title") or "").strip()
        if not title:
            raise IntegrationArgumentError("Укажите title для issue")
        body_json: Dict[str, Any] = {"title": title, "body": str(args.get("body") or "")}
        from .integrations_github import _list as _gh_list
        for key in ("labels", "assignees"):
            if _gh_list(args.get(key)):
                body_json[key] = _gh_list(args.get(key))
        row = _github("POST", "/repos/%s/issues" % _repo(args), json_body=body_json)
        return {"number": row.get("number"), "url": row.get("html_url"), "state": row.get("state")}
    if tool == "github_write_file":
        repo, path = _repo(args), str(args.get("path") or "").strip().lstrip("/")
        if not path:
            raise IntegrationArgumentError("Укажите path файла")
        content = str(args.get("content") or "")
        message = str(args.get("message") or "").strip() or "MemuAI update"
        if args.get("content_base64"):

            encoded = re.sub(r"\s+", "", str(args["content_base64"]))
            try:
                base64.b64decode(encoded, validate=True)
            except Exception as exc:
                raise IntegrationArgumentError("content_base64 — не base64") from exc
        else:
            encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        body: Dict[str, Any] = {"message": message, "content": encoded}
        if args.get("branch"):
            body["branch"] = str(args.get("branch"))
        try:
            existing = _github("GET", "/repos/%s/contents/%s" % (repo, quote(path, safe="/")), params={"ref": body["branch"]} if body.get("branch") else None)
        except IntegrationError as exc:

            if "(404)" not in str(exc):
                raise
            existing = None
        if isinstance(existing, list):
            raise IntegrationArgumentError("По этому пути лежит каталог, а не файл: %s" % path)
        if isinstance(existing, dict) and existing.get("sha"):
            body["sha"] = existing["sha"]
        row = _github("PUT", "/repos/%s/contents/%s" % (repo, quote(path, safe="/")), json_body=body)
        commit = row.get("commit") or {}
        return {"path": path, "commit": str(commit.get("sha") or "")[:10], "url": (row.get("content") or {}).get("html_url"),
                "created": "sha" not in body}

    from . import integrations_github
    return integrations_github.run(tool, args)

def _google_access_token(service: str, *, force: bool = False) -> str:
    """Round 30: the access token of the shared Google account (app/google_account.py), cached here per service."""
    cached = _tokens.get(service) or {}
    if not force and cached.get("token") and float(cached.get("expires") or 0) > time.time() + 60:
        return str(cached["token"])
    from . import google_account
    try:
        token = google_account.access_token(_service_meta(service)["google"], force=force)
    except google_account.GoogleAccountError as exc:
        if exc.code in ("not_connected", "disabled", "need_consent", "no_client"):
            raise IntegrationAccountError(str(exc)) from exc
        raise IntegrationError(str(exc)) from exc
    _tokens[service] = {"token": token, "expires": time.time() + 600}
    return token

def _google_error(service: str, response: httpx.Response) -> IntegrationError:
    """Google APIs say *why* in error.errors[].reason / error.status; a 403 is often a rate limit."""
    status = response.status_code
    data = _error_payload(response).get("error")
    data = data if isinstance(data, dict) else {}
    message = str(data.get("message") or "") or _short(response.text)
    reasons = {str((row or {}).get("reason") or "") for row in (data.get("errors") or []) if isinstance(row, dict)}
    for row in data.get("details") or []:
        if isinstance(row, dict) and row.get("reason"):
            reasons.add(str(row["reason"]))
    reasons.add(str(data.get("status") or ""))
    title = _service_meta(service)["title"]
    api_name, _api_id = _google_api(service)
    if reasons & {"accessNotConfigured", "SERVICE_DISABLED"} or "has not been used in project" in message:

        name, url = _disabled_api(service, data, message)
        return IntegrationError("%s выключен в вашем проекте Google Cloud — поэтому MemuAI не может работать с %s. "
                                "Откройте ссылку, нажмите «Включить» (Enable) и через 1–2 минуты повторите: %s "
                                "Все нужные API включаются одной кнопкой в Настройки → Интеграции → Google → «Проверить API»."
                                % (name, title, url))
    if status == 429 or reasons & {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded",
                                   "dailyLimitExceeded", "RESOURCE_EXHAUSTED", "RATE_LIMIT_EXCEEDED"}:
        return IntegrationError("%s: превышен лимит запросов Google (%s). Подождите минуту и повторите." % (title, status))
    if reasons & {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"} or "scope" in message.lower():
        return IntegrationError("%s: Google не выдал нужный доступ (scope). Откройте Настройки → Интеграции → Google, нажмите "
                                "«Расширить доступ» у %s и на странице Google отметьте все галочки." % (title, title))
    if status == 401:
        return IntegrationError("%s отклонил ключ доступа (401: %s). Войдите в Google заново: Настройки → Интеграции."
                                % (title, message))
    if status == 403:
        return IntegrationError("Google отказал в доступе (403: %s). Проверьте, включён ли %s и выданы ли scope."
                                % (message, api_name))
    if status == 404:
        return IntegrationError("%s: не найдено (404). Проверьте ID файла, папки, календаря или события." % title)
    if status >= 500:
        return IntegrationError("%s временно недоступен (%s). Попробуйте позже." % (title, status))
    return IntegrationError("Google %s: %s" % (status, message))

GOOGLE_APIS = {"gmail": ("Gmail API", "gmail.googleapis.com"),
               "google_calendar": ("Google Calendar API", "calendar-json.googleapis.com"),
               "google_drive": ("Google Drive API", "drive.googleapis.com"),
               "google_docs": ("Google Docs API", "docs.googleapis.com"),
               "google_sheets": ("Google Sheets API", "sheets.googleapis.com"),
               "google_tasks": ("Google Tasks API", "tasks.googleapis.com"),
               "google_contacts": ("People API", "people.googleapis.com")}

def _google_api(service: str) -> Tuple[str, str]:
    return GOOGLE_APIS.get(service, ("Google API", ""))

def _disabled_api(service: str, data: Dict[str, Any], message: str) -> Tuple[str, str]:
    """(API title, «Enable» link) from a SERVICE_DISABLED error: ErrorInfo metadata → Help links → the message → ours."""
    name, api = _google_api(service)
    url = ""
    for row in data.get("details") or []:
        if not isinstance(row, dict):
            continue
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if meta.get("service"):
            api = str(meta["service"])
            name = str(meta.get("serviceTitle") or "") or next((t for t, a in GOOGLE_APIS.values() if a == api), api)
        if meta.get("activationUrl"):
            url = str(meta["activationUrl"])
        for link in row.get("links") or []:
            if isinstance(link, dict) and not url and "console" in str(link.get("url") or ""):
                url = str(link["url"])
    if not url:
        found = re.search(r"https://console\.(?:developers|cloud)\.google\.com/apis/api/[^\s\"']+", message)
        url = found.group(0).rstrip(".") if found else ""
    if not url:
        from . import google_account
        url = google_account.api_url(api) if api else google_account.enable_url()
    return name, url

def _google(service: str, method: str, url: str, *, params: Optional[Dict[str, Any]] = None,
            json_body: Optional[Dict[str, Any]] = None, content: Optional[bytes] = None,
            content_type: str = "", raw: bool = False, allow: Tuple[int, ...] = ()) -> Any:
    for attempt in range(2):
        headers = {"Authorization": "Bearer " + _google_access_token(service, force=attempt > 0)}
        if content_type:
            headers["Content-Type"] = content_type
        response = _send(_service_meta(service)["title"], method, url, headers=headers, params=params,
                         json=json_body, content=content)
        if response.status_code == 401 and attempt == 0:

            _tokens.pop(service, None)
            continue
        break
    if response.status_code in allow:
        return {"_status": response.status_code}
    if response.status_code >= 400:
        raise _google_error(service, response)
    if raw:
        return response.text
    if not response.content:
        return {}
    try:
        return response.json()
    except Exception:
        return {"text": response.text[:MAX_TEXT]}

def _google_stream(service: str, url: str, params: Dict[str, Any]) -> Tuple[bytes, bool]:
    def fail(response: httpx.Response) -> None:
        if response.status_code == 401:
            _tokens.pop(service, None)
        raise _google_error(service, response)
    token = _google_access_token(service)
    data, cut, _type = _stream_text(_service_meta(service)["title"], url, {"Authorization": "Bearer " + token},
                                    params=params, on_error=fail)
    return data, cut

def _drive_quote(value: str) -> str:
    """A string literal of the Drive query language: backslash and apostrophe escaped (not dropped)."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"

def _is_text_mime(mime: str) -> bool:
    mime = (mime or "").lower()
    return (mime.startswith("text/") or mime in ("application/json", "application/xml", "application/javascript",
                                                 "application/x-yaml", "application/yaml", "application/x-sh",
                                                 "application/sql", "application/x-python", "application/toml",
                                                 "application/x-ndjson", "application/ld+json", "image/svg+xml")
            or mime.endswith(("+json", "+xml")) or not mime or mime == "application/octet-stream")

def _multipart(metadata: Dict[str, Any], content: bytes, media_type: str) -> Tuple[bytes, str]:
    boundary = "memuai-" + uuid.uuid4().hex
    body = (("--%s\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n" % boundary).encode("utf-8")
            + json.dumps(metadata, ensure_ascii=False).encode("utf-8")
            + ("\r\n--%s\r\nContent-Type: %s\r\n\r\n" % (boundary, media_type)).encode("utf-8")
            + content + ("\r\n--%s--\r\n" % boundary).encode("utf-8"))
    return body, "multipart/related; boundary=" + boundary

EXPORT_TYPES = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}

GOOGLE_NATIVE_UPLOAD = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}

def _drive_tool(tool: str, args: Dict[str, Any]) -> Any:
    limit = _int(args.get("limit"), 25, 1, 100)
    if tool == "drive_list_files":
        trashed = str(args.get("trashed") or "").lower() in ("1", "true", "yes")
        clauses = ["trashed = %s" % ("true" if trashed else "false")]
        mime = str(args.get("mime") or "").strip().lower()
        if mime:

            mime = {"folder": "application/vnd.google-apps.folder", "doc": "application/vnd.google-apps.document",
                    "document": "application/vnd.google-apps.document", "sheet": "application/vnd.google-apps.spreadsheet",
                    "spreadsheet": "application/vnd.google-apps.spreadsheet", "slides": "application/vnd.google-apps.presentation",
                    "presentation": "application/vnd.google-apps.presentation", "pdf": "application/pdf"}.get(mime, mime)
            if mime in ("image", "video", "audio", "text"):
                clauses.append("mimeType contains %s" % _drive_quote(mime + "/"))
            else:
                clauses.append("mimeType = %s" % _drive_quote(mime))
        if str(args.get("shared_with_me") or "").lower() in ("1", "true", "yes"):
            clauses.append("sharedWithMe = true")
        query = str(args.get("query") or "").strip()
        if query:

            if str(args.get("search_content") or "").lower() in ("1", "true", "yes"):
                clauses.append("(name contains %s or fullText contains %s)" % (_drive_quote(query), _drive_quote(query)))
            else:
                clauses.append("name contains %s" % _drive_quote(query))
        if args.get("folder_id"):
            clauses.append("%s in parents" % _drive_quote(str(args["folder_id"]).strip()))
        params: Dict[str, Any] = {
            "q": " and ".join(clauses),
            "pageSize": limit,
            "orderBy": "folder,modifiedTime desc",
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,size,webViewLink,parents)",
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
        }
        if args.get("page_token"):
            params["pageToken"] = str(args["page_token"])
        rows = _google("google_drive", "GET", DRIVE_API + "/files", params=params)
        return {"files": rows.get("files") or [], "more": bool(rows.get("nextPageToken")),
                "next_page_token": rows.get("nextPageToken") or ""}
    if tool == "drive_read_file":
        file_id = str(args.get("file_id") or "").strip()
        if not file_id:
            raise IntegrationArgumentError("Укажите file_id")

        file_ref = quote(file_id, safe="")
        meta = _google("google_drive", "GET", DRIVE_API + "/files/" + file_ref,
                       params={"fields": "id,name,mimeType,size", "supportsAllDrives": "true"})
        mime = str(meta.get("mimeType") or "")
        size = _int(meta.get("size"), 0, 0, 1 << 62)
        if mime in EXPORT_TYPES:
            data, cut = _google_stream("google_drive", DRIVE_API + "/files/%s/export" % file_ref,
                                       {"mimeType": EXPORT_TYPES[mime]})
        elif mime.startswith("application/vnd.google-apps"):
            raise IntegrationArgumentError("Файл типа %s нельзя прочитать текстом" % mime)
        elif not _is_text_mime(mime):

            return {"name": meta.get("name"), "mime": mime, "size": size, "binary": True, "text": "",
                    "note": "Это не текстовый файл (%s, %d байт): прочитать его текстом нельзя." % (mime, size)}
        else:
            data, cut = _google_stream("google_drive", DRIVE_API + "/files/" + file_ref,
                                       {"alt": "media", "supportsAllDrives": "true"})
        body, binary = _decode(data)
        if binary:
            return {"name": meta.get("name"), "mime": mime, "size": size, "binary": True, "text": "",
                    "note": "Двоичный файл: прочитать его текстом нельзя."}
        return {"name": meta.get("name"), "mime": mime, "text": body[:MAX_TEXT], "truncated": cut or len(body) > MAX_TEXT}
    if tool == "drive_create_file":
        name = str(args.get("name") or "").strip() or "memuai.txt"
        mime = str(args.get("mime") or "text/plain").strip() or "text/plain"
        metadata: Dict[str, Any] = {"name": name, "mimeType": mime}
        if args.get("folder_id"):
            metadata["parents"] = [str(args["folder_id"]).strip()]

        media = GOOGLE_NATIVE_UPLOAD.get(mime, mime)
        body, content_type = _multipart(metadata, str(args.get("content") or "").encode("utf-8"), media)
        created = _google("google_drive", "POST", DRIVE_UPLOAD + "/files",
                          params={"uploadType": "multipart", "fields": "id,name,webViewLink", "supportsAllDrives": "true"},
                          content=body, content_type=content_type)
        return {"id": str(created.get("id") or ""), "name": created.get("name"), "link": created.get("webViewLink")}
    if tool == "drive_upload_file":
        return _drive_upload(args)
    if tool == "drive_update_file":
        file_id = str(args.get("file_id") or "").strip()
        if not file_id:
            raise IntegrationArgumentError("Укажите file_id")
        mime = str(args.get("mime") or "text/plain").strip() or "text/plain"
        _google("google_drive", "PATCH", DRIVE_UPLOAD + "/files/" + quote(file_id, safe=""),
                params={"uploadType": "media", "supportsAllDrives": "true"},
                content=str(args.get("content") or "").encode("utf-8"),
                content_type=GOOGLE_NATIVE_UPLOAD.get(mime, mime))
        return {"id": file_id, "updated": True}

    return integrations_drive.run(tool, args)

def _drive_upload(args: Dict[str, Any]) -> Dict[str, Any]:
    """Round 30: a local file (a report, a photo, a PDF) → Google Drive, as is (binary is fine)."""
    import mimetypes
    raw = str(args.get("path") or "").strip().strip('"')
    if not raw:
        raise IntegrationArgumentError("Укажите path — полный путь к файлу на этом компьютере")
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not path.is_absolute() or not path.is_file():
        raise IntegrationArgumentError("Файл не найден: %s (нужен полный путь)" % raw)
    size = path.stat().st_size
    if size > MAX_UPLOAD:
        raise IntegrationArgumentError("Файл больше %d МБ — загрузите его в Google Drive вручную" % (MAX_UPLOAD // (1024 * 1024)))
    name = str(args.get("name") or "").strip() or path.name
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    metadata: Dict[str, Any] = {"name": name}
    if args.get("folder_id"):
        metadata["parents"] = [str(args["folder_id"]).strip()]
    body, content_type = _multipart(metadata, path.read_bytes(), mime)
    created = _google("google_drive", "POST", DRIVE_UPLOAD + "/files",
                      params={"uploadType": "multipart", "fields": "id,name,webViewLink,size", "supportsAllDrives": "true"},
                      content=body, content_type=content_type)
    return {"id": str(created.get("id") or ""), "name": created.get("name"), "size": size, "link": created.get("webViewLink")}

def _calendar_id(args: Dict[str, Any]) -> str:
    value = str(args.get("calendar_id") or "").strip()
    if not value:
        from . import google_account
        value = google_account.default_calendar()

    return quote(value or "primary", safe="")

def _event_body(args: Dict[str, Any]) -> Dict[str, Any]:
    """Event fields from the model's arguments (create: all; update: only what is given)."""
    timezone = str(args.get("timezone") or "").strip()

    def moment(value: str) -> Dict[str, Any]:
        if len(value) == 10:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise IntegrationArgumentError("Неверная дата: %s (нужно ГГГГ-ММ-ДД)" % value) from exc
            return {"date": value}
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise IntegrationArgumentError("Неверное время: %s (нужен ISO-8601, например 2026-10-08T15:00)" % value) from exc
        slot: Dict[str, Any] = {}
        if timezone:

            slot["dateTime"] = parsed.replace(tzinfo=None).isoformat(timespec="seconds") if parsed.tzinfo is None \
                else parsed.isoformat(timespec="seconds")
            slot["timeZone"] = timezone
        else:

            slot["dateTime"] = (parsed.astimezone() if parsed.tzinfo is None else parsed).isoformat(timespec="seconds")
        return slot

    body: Dict[str, Any] = integrations_gcal.extra_event_fields(args)
    start, end = str(args.get("start") or "").strip(), str(args.get("end") or "").strip()
    if start and end:
        start_slot, end_slot = moment(start), moment(end)
        if "date" in start_slot and "date" in end_slot and end_slot["date"] <= start_slot["date"]:

            end_slot["date"] = (date.fromisoformat(start_slot["date"]) + timedelta(days=1)).isoformat()
        elif ("date" in start_slot) != ("date" in end_slot):
            raise IntegrationArgumentError("Начало и конец должны быть одного вида: обе даты (весь день) или оба времени")
        elif "dateTime" in start_slot and not timezone:
            if datetime.fromisoformat(end_slot["dateTime"]) <= datetime.fromisoformat(start_slot["dateTime"]):
                raise IntegrationArgumentError("Конец события должен быть позже начала")
        body["start"], body["end"] = start_slot, end_slot
    if str(args.get("summary") or "").strip():
        body["summary"] = str(args["summary"]).strip()
    if args.get("description") is not None and str(args.get("description")) != "":
        body["description"] = str(args["description"])
    if args.get("location"):
        body["location"] = str(args["location"])
    return body

def _calendar_tool(tool: str, args: Dict[str, Any]) -> Any:
    if tool == "calendar_list_calendars":
        rows = _google("google_calendar", "GET", CALENDAR_API + "/users/me/calendarList", params={"maxResults": 250})
        return {"calendars": [{"id": row.get("id"), "summary": row.get("summary"), "primary": row.get("primary")} for row in (rows.get("items") or [])]}
    if tool == "calendar_list_events":
        days = _int(args.get("days"), 14, 1, 180)
        limit = _int(args.get("limit"), 25, 1, 100)
        now = time.time()
        params = {
            "timeMin": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "timeMax": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + days * 86400)),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": limit,
        }

        for key, field in (("time_min", "timeMin"), ("time_max", "timeMax")):
            if str(args.get(key) or "").strip():
                params[field] = integrations_gcal.rfc3339(str(args[key]), end=key == "time_max")
        if args.get("query"):
            params["q"] = str(args["query"])
        rows = _google("google_calendar", "GET", CALENDAR_API + "/calendars/%s/events" % _calendar_id(args), params=params)
        return {"events": [integrations_gcal.event_row(row) for row in (rows.get("items") or [])]}
    if tool == "calendar_create_event":
        summary = str(args.get("summary") or "").strip()
        start, end = str(args.get("start") or "").strip(), str(args.get("end") or "").strip()
        if not summary or not start or not end:
            raise IntegrationArgumentError("Нужны summary, start и end (ISO-8601)")
        body = _event_body(args)
        body.setdefault("description", "")
        row = _google("google_calendar", "POST", CALENDAR_API + "/calendars/%s/events" % _calendar_id(args), json_body=body,
                      params=integrations_gcal.write_params(args, body))
        return {"id": row.get("id"), "link": row.get("htmlLink"), "status": row.get("status"),
                **integrations_gcal.created_extras(row)}
    if tool == "calendar_update_event":
        event_id = str(args.get("event_id") or "").strip()
        if not event_id:
            raise IntegrationArgumentError("Укажите event_id (его даёт calendar_list_events)")
        if bool(str(args.get("start") or "").strip()) != bool(str(args.get("end") or "").strip()):
            raise IntegrationArgumentError("Чтобы перенести событие, укажите и start, и end")
        body = _event_body(args)
        if not body:
            raise IntegrationArgumentError("Нечего менять: укажите summary, start/end, description или location")
        row = _google("google_calendar", "PATCH", CALENDAR_API + "/calendars/%s/events/%s"
                      % (_calendar_id(args), quote(event_id, safe="")), json_body=body,
                      params=integrations_gcal.write_params(args, body))
        return {"id": row.get("id"), "link": row.get("htmlLink"), "status": row.get("status"), "updated": True,
                **integrations_gcal.created_extras(row)}
    if tool == "calendar_delete_event":
        event_id = str(args.get("event_id") or "").strip()
        if not event_id:
            raise IntegrationArgumentError("Укажите event_id")
        result = _google("google_calendar", "DELETE", CALENDAR_API + "/calendars/%s/events/%s" % (_calendar_id(args), quote(event_id, safe="")),
                         allow=(410,), params=integrations_gcal.write_params(args, {}))

        return {"id": event_id, "deleted": True, **({"already_deleted": True} if result.get("_status") == 410 else {})}

    return integrations_gcal.run(tool, args)

PREFIX_SERVICE = {"github_": "github", "drive_": "google_drive", "calendar_": "google_calendar", "gmail_": "gmail",
                  "notion_": "notion", "gdocs_": "google_docs", "gsheets_": "google_sheets", "gtasks_": "google_tasks",
                  "gcontacts_": "google_contacts", "telegram_use_": "telegram_use", "chrome_": "chrome_extension"}

def service_for_tool(tool: str) -> str:
    name = str(tool or "").strip().lower()
    for prefix, service in PREFIX_SERVICE.items():
        if name.startswith(prefix):
            return service
    return ""

TOOL_PREFIXES = tuple(PREFIX_SERVICE)

def _check_scopes(service: str, tool: str) -> None:
    """Round 33: a tool that needs more than the service's basic grant fails *before* any request, with the remedy."""
    need = TOOL_SCOPES.get(tool)
    if not need or SERVICES[service].get("kind") != "google":
        return
    from . import google_account
    try:
        scopes = google_account.auth().granted_scopes() if google_account.auth().connected() else set()
    except Exception:
        return
    if scopes and not (need & scopes):
        title = SERVICES[service]["title"]
        raise IntegrationAccountError(
            "%s: для «%s» нужен расширенный доступ — Google выдал MemuAI только базовые права. Откройте Настройки → "
            "Интеграции → Google и нажмите «Расширить доступ» у %s (Google спросит разрешение ещё раз, всё прежнее "
            "сохранится)." % (title, tool, title))

def run_tool(tool: str, args: Optional[Dict[str, Any]] = None) -> Any:
    """Выполняет инструмент интеграции и возвращает готовые к JSON данные."""
    name = str(tool or "").strip().lower()
    payload = args if isinstance(args, dict) else {}
    service = service_for_tool(name)
    if not service:
        raise IntegrationArgumentError("Неизвестный инструмент интеграции: %s" % tool)
    if service in ("telegram_use", "chrome_extension"):
        try:
            if service == "telegram_use":
                from . import telegram_use
                return telegram_use.run_tool(name, payload)
            from . import browser_extension
            return browser_extension.run_sync(name, payload)
        except (ValueError, RuntimeError, PermissionError) as exc:
            raise IntegrationError(str(exc)) from exc
    try:
        _check_scopes(service, name)
        if service == "github":
            result = _github_tool(name, payload)
        elif service == "google_drive":
            result = _drive_tool(name, payload)
        elif service == "gmail":
            result = integrations_gmail.run(name, payload)
        elif service == "notion":
            from . import integrations_notion
            result = integrations_notion.run(name, payload)
        elif service in ("google_docs", "google_sheets", "google_tasks", "google_contacts"):
            result = integrations_gworkspace.run(service, name, payload)
        else:
            result = _calendar_tool(name, payload)
    except IntegrationError as exc:
        if not isinstance(exc, (IntegrationArgumentError, IntegrationAccountError)):
            _remember(service, error=str(exc))
        raise
    except httpx.HTTPError as exc:
        error = _network_error(_service_meta(service)["title"], exc)
        _remember(service, error=str(error))
        raise error from exc
    try:
        _clear_error_after_success(service)
    except Exception:
        pass
    return result

def test_connection(service: str) -> Dict[str, Any]:
    """Проверяет подключение одним дешёвым запросом."""
    _service_meta(service)
    try:
        if service == "github":

            info = integrations_github.token_info()
            account = str(info.get("login") or "")
            detail = "Репозиториев: %s" % ((info.get("public_repos") or 0) + (info.get("private_repos") or 0))
            if info.get("kind") == "classic":
                detail += " · classic: %s" % (", ".join(info.get("scopes") or []) or "без scope")
                if info.get("missing"):
                    detail += " · не хватает: %s" % ", ".join(info["missing"])
            elif info.get("kind") == "fine-grained":
                detail += " · fine-grained"
            _remember(service, account=account)
            return {"service": service, "ok": True, "account": account, "detail": detail, "token": info}
        elif service == "google_drive":
            about = _google("google_drive", "GET", DRIVE_API + "/about", params={"fields": "user(emailAddress,displayName)"})
            user = about.get("user") or {}
            account = str(user.get("emailAddress") or user.get("displayName") or "")
            detail = "Google Drive ответил"
        elif service == "gmail":
            from .integrations_gmail import GMAIL_API
            profile = _google("gmail", "GET", GMAIL_API + "/profile")
            account = str(profile.get("emailAddress") or "")
            detail = "Писем: %s" % (profile.get("messagesTotal") or 0)
        elif service == "notion":
            from . import integrations_notion
            account = integrations_notion.test()["account"]
            detail = "Notion ответил"
        elif service in GOOGLE_CHECKS and service != "google_calendar":
            ok = google_check_one(service)
            if not ok["ok"]:
                raise IntegrationError(ok["error"])
            account = ""
            detail = "%s отвечает" % _google_api(service)[0]
        else:
            rows = _google("google_calendar", "GET", CALENDAR_API + "/users/me/calendarList", params={"maxResults": 250})
            items = rows.get("items") or []
            primary = next((row for row in items if row.get("primary")), items[0] if items else {})
            account = str(primary.get("id") or "")
            detail = "Календарей: %s" % len(items)
    except IntegrationError as exc:
        _remember(service, error=str(exc))
        return {"service": service, "ok": False, "error": str(exc)}
    except httpx.HTTPError as exc:
        message = str(_network_error(_service_meta(service)["title"], exc))
        _remember(service, error=message)
        return {"service": service, "ok": False, "error": message}
    _remember(service, account=account)
    return {"service": service, "ok": True, "account": account, "detail": detail}
