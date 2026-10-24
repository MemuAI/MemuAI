"""Round 29 «Синхронизация»: Google Drive API v3 and OAuth 2.0 for the backup feature.

* **OAuth** — the installed-app *loopback* flow with PKCE (RFC 8252 / RFC 7636):
  MemuAI opens a one-shot HTTP listener on ``127.0.0.1:<random port>``, opens
  Google's consent page in the default browser, receives ``?code=…&state=…`` on
  the listener and exchanges it for a refresh token.  Scopes: ``drive.appdata``
  (the hidden per-app folder — the default place for backups) and ``drive.file``
  (only files this app created — used when the user wants a visible
  «MemuAI Backups» folder).  Nothing else in the user's Drive is readable.
* **Client id** — Google requires an OAuth client of type «Desktop app».  The user
  can paste their own (guide: Documentation → «Синхронизация»); a developer can
  ship one by filling :data:`BUNDLED_CLIENT_ID` / :data:`BUNDLED_CLIENT_SECRET`
  (for a Desktop client the «secret» is not confidential by Google's own design).
* **Tokens** — the refresh token is kept in :class:`gdrive_sync_crypto.SecretStore`
  (DPAPI on Windows); the access token only in memory.  Tokens never reach logs
  or HTTP responses.
* **Drive** — :class:`DriveTarget` implements the small storage interface of
  :mod:`app.gdrive_sync` (``list / put_file / put_bytes / get_file / get_bytes /
  delete``) with resumable chunked uploads, an optional bandwidth limit and
  retries for 429/5xx/network errors.

Every HTTP call goes through one ``httpx.Client`` whose transport can be
replaced; ``tests/round29_sync_test.py`` runs the whole flow against an
in-memory fake of the Drive API (``httpx.MockTransport``).
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import httpx

log = logging.getLogger("memuai.sync.drive")

#: A developer may bundle an OAuth client of type «Desktop app» here; the user's own client wins.
BUNDLED_CLIENT_ID = ""
BUNDLED_CLIENT_SECRET = ""

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
SCOPE_APPDATA = "https://www.googleapis.com/auth/drive.appdata"
SCOPE_FILE = "https://www.googleapis.com/auth/drive.file"
SCOPES = (SCOPE_APPDATA, SCOPE_FILE)
#: Round 30: who signed in (Gmail- or Calendar-only accounts have no Drive /about to read the e-mail from).
SCOPE_EMAIL = "https://www.googleapis.com/auth/userinfo.email"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
FOLDER_MIME = "application/vnd.google-apps.folder"
VISIBLE_FOLDER = "MemuAI Backups"
CHUNK = 8 * 1024 * 1024          # resumable chunk: a multiple of 256 KiB
SIMPLE_LIMIT = 4 * 1024 * 1024   # smaller bodies use one multipart request
FLOW_TIMEOUT = 300.0
RETRIES = 4


class DriveError(RuntimeError):
    """A Drive/OAuth failure with a message in both languages."""

    def __init__(self, ru: str, en: str = "", *, code: str = "", status: int = 0) -> None:
        super().__init__(ru)
        self.ru = ru
        self.en = en or ru
        self.code = code
        self.status = status


class DriveAuthError(DriveError):
    """The user has to connect (again): no token, revoked token, wrong client."""


def _client_factory(transport: Optional[httpx.BaseTransport]) -> httpx.Client:
    # Google is reached directly: the user's system proxy is honoured (trust_env) as for
    # every other Google integration in MemuAI.
    return httpx.Client(timeout=httpx.Timeout(60, connect=15), transport=transport, follow_redirects=False)


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode("ascii").rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    return verifier, challenge


_DONE_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>MemuAI</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;background:#1a1a1c;color:#eee;display:grid;place-items:center;height:100vh;margin:0}}
.card{{max-width:460px;padding:28px 32px;border-radius:16px;background:#26262a;box-shadow:0 10px 40px #0006}}
h1{{font-size:20px;margin:0 0 8px}}p{{margin:6px 0;color:#bbb}}.ok{{color:#7fd2be}}.bad{{color:#f4a09a}}</style></head>
<body><div class="card"><h1 class="{cls}">{title_ru}</h1><p>{text_ru}</p><hr style="border:0;border-top:1px solid #333">
<h1 class="{cls}" style="font-size:17px">{title_en}</h1><p>{text_en}</p></div></body></html>"""


class DriveAuth:
    """OAuth state of the sync feature: client, refresh token, the running consent flow."""

    def __init__(self, store, *, client_override: Callable[[], tuple[str, str]] | None = None,
                 transport: Optional[httpx.BaseTransport] = None,
                 open_browser: Callable[[str], Any] | None = None) -> None:
        self.store = store
        self.client_override = client_override
        self.transport = transport
        self.open_browser = open_browser
        self._access: tuple[str, float] = ("", 0.0)
        self._lock = threading.Lock()
        self._flow: Dict[str, Any] = {"state": "idle"}
        self._server: Optional[HTTPServer] = None
        self.on_connected: Callable[[Dict[str, Any]], None] | None = None

    # ---------------------------------------------------------------- client
    def client(self) -> tuple[str, str, str]:
        """``(client_id, client_secret, source)``; source = user | bundled | ''."""
        cid, secret = ("", "")
        if self.client_override:
            cid, secret = self.client_override()
        if cid:
            return cid, secret, "user"
        if BUNDLED_CLIENT_ID:
            return BUNDLED_CLIENT_ID, BUNDLED_CLIENT_SECRET, "bundled"
        # Round 30: a client baked in by build.py (MEMUAI_GOOGLE_CLIENT_ID) or set in the environment.
        from .oauth_config import client as configured_client
        cid, secret, source = configured_client("google")
        return (cid, secret, "bundled") if cid else ("", "", "")

    def connected(self) -> bool:
        return bool(self.store.get("google_refresh_token"))

    def http(self) -> httpx.Client:
        return _client_factory(self.transport)

    # ------------------------------------------------------------- the flow
    def granted_scopes(self) -> set:
        """Scopes of the stored refresh token (Round 29: «Add from Google Drive» asks for one more, incrementally)."""
        if not self.connected():
            return set()
        recorded = (self.store.get("google_scopes") or "").split()
        return set(recorded) if recorded else set(SCOPES)

    def has_backup_scopes(self) -> bool:
        """Round 30: the token can store backups (it may come from Integrations with Gmail/Calendar scopes only)."""
        granted = self.granted_scopes()
        return bool(granted) and set(SCOPES) <= granted

    def _remember_scopes(self, payload: Dict[str, Any]) -> None:
        scope = str(payload.get("scope") or "").strip()
        if scope:
            self.store.set("google_scopes", " ".join(sorted(set(scope.split()))))

    def start_flow(self, extra_scopes: tuple = (), purpose: str = "sync", base_scopes: tuple = SCOPES) -> Dict[str, Any]:
        """Open Google's consent page. ``base_scopes`` + ``extra_scopes`` are asked for; scopes granted earlier stay
        (``include_granted_scopes``), so Settings → Integrations asks only for the services just switched on."""
        cid, secret, source = self.client()
        if not cid:
            raise DriveAuthError("Сначала укажите OAuth Client ID (инструкция — в документации «Синхронизация»)",
                                 "Enter an OAuth Client ID first (see the «Sync» documentation)", code="no_client")
        self.cancel_flow()
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(24)
        auth = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # never log codes
                return

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                if "state" not in query and "code" not in query and "error" not in query:
                    self.send_response(404)
                    self.end_headers()
                    return
                ok, title_ru, title_en, text_ru, text_en = auth._finish(query, state, verifier, redirect)
                page = _DONE_PAGE.format(cls="ok" if ok else "bad", title_ru=html.escape(title_ru),
                                         title_en=html.escape(title_en), text_ru=html.escape(text_ru),
                                         text_en=html.escape(text_en)).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(page)
                # Round 30: close THIS flow's listener — a late «close the current one» used to shut the next flow's port
                # when two consents ran back to back (Integrations adds services one after another).
                threading.Thread(target=auth._close_server, args=(server,), daemon=True).start()

        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.timeout = 1.0
        redirect = f"http://127.0.0.1:{server.server_address[1]}/"
        self._server = server
        params = {"client_id": cid, "redirect_uri": redirect, "response_type": "code",
                  "scope": " ".join(dict.fromkeys(tuple(base_scopes) + tuple(extra_scopes))), "code_challenge": challenge, "code_challenge_method": "S256",
                  "state": state, "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"}
        url = AUTH_URL + "?" + urllib.parse.urlencode(params)
        self._flow = {"state": "waiting", "started": time.time(), "auth_url": url, "source": source, "purpose": purpose}

        def serve() -> None:
            deadline = time.time() + FLOW_TIMEOUT
            while self._server is server and time.time() < deadline and self._flow.get("state") == "waiting":
                try:
                    server.handle_request()
                except Exception:
                    break
            if self._flow.get("state") == "waiting" and self._server is server:
                self._flow = {"state": "error", "error_ru": "Время ожидания входа истекло", "error_en": "Sign-in timed out"}
            self._close_server(server)

        threading.Thread(target=serve, name="memuai-sync-oauth", daemon=True).start()
        if self.open_browser:
            try:
                self.open_browser(url)
            except Exception as exc:  # the UI also shows the link
                log.info("could not open the browser for Google sign-in: %s", type(exc).__name__)
        return {"auth_url": url, "state": "waiting"}

    def _close_server(self, server: Optional[HTTPServer] = None) -> None:
        target = server or self._server
        if target is None:
            return
        if self._server is target:
            self._server = None
        try:
            target.server_close()
        except Exception:
            pass

    def cancel_flow(self) -> None:
        if self._flow.get("state") == "waiting":
            self._flow = {"state": "idle"}
        self._close_server()

    def flow_status(self) -> Dict[str, Any]:
        flow = dict(self._flow)
        flow.pop("verifier", None)
        return flow

    def _finish(self, query: Dict[str, list], state: str, verifier: str, redirect: str):
        got_state = (query.get("state") or [""])[0]
        if not secrets.compare_digest(got_state, state):
            self._flow = {"state": "error", "error_ru": "Ответ Google не совпал с запросом (state)",
                          "error_en": "Google's answer did not match the request (state)"}
            return False, "Вход не выполнен", "Sign-in failed", "Попробуйте ещё раз из MemuAI.", "Try again from MemuAI."
        error = (query.get("error") or [""])[0]
        if error:
            denied = error == "access_denied"
            self._flow = {"state": "error",
                          "error_ru": "Доступ не выдан" if denied else f"Google вернул ошибку: {error[:60]}",
                          "error_en": "Access was not granted" if denied else f"Google returned an error: {error[:60]}"}
            return False, "Доступ не выдан", "Access not granted", "Окно можно закрыть.", "You can close this tab."
        code = (query.get("code") or [""])[0]
        try:
            self.exchange(code, verifier, redirect)
            info = self.account_info()
        except DriveError as exc:
            self._flow = {"state": "error", "error_ru": exc.ru, "error_en": exc.en}
            return False, "Вход не выполнен", "Sign-in failed", exc.ru, exc.en
        self._flow = {"state": "done", "account": info, "purpose": self._flow.get("purpose", "sync")}
        if self.on_connected:
            try:
                self.on_connected(info)
            except Exception:
                log.exception("sync: on_connected hook failed")
        if self._flow.get("purpose") == "integrations":
            return (True, "Google подключён", "Google connected",
                    "Вернитесь в MemuAI — окно можно закрыть.", "Return to MemuAI — you can close this tab.")
        return (True, "Google Drive подключён", "Google Drive connected",
                "Вернитесь в MemuAI — окно можно закрыть.", "Return to MemuAI — you can close this tab.")

    def exchange(self, code: str, verifier: str, redirect: str) -> None:
        cid, secret, _ = self.client()
        data = {"code": code, "client_id": cid, "code_verifier": verifier, "redirect_uri": redirect,
                "grant_type": "authorization_code"}
        if secret:
            data["client_secret"] = secret
        payload = self._token_request(data)
        refresh = str(payload.get("refresh_token") or "")
        if not refresh:
            raise DriveAuthError("Google не выдал refresh token. Отзовите доступ MemuAI в аккаунте Google и подключите снова.",
                                 "Google issued no refresh token. Remove MemuAI's access in your Google account and connect again.",
                                 code="no_refresh")
        self.store.set("google_refresh_token", refresh)
        self.store.delete("google_scopes")
        self._remember_scopes(payload)
        with self._lock:
            self._access = (str(payload.get("access_token") or ""), time.time() + float(payload.get("expires_in") or 3000) - 60)

    def _token_request(self, data: Dict[str, str]) -> Dict[str, Any]:
        try:
            with self.http() as client:
                response = client.post(TOKEN_URL, data=data)
        except httpx.HTTPError as exc:
            raise DriveError("Нет связи с Google", "Cannot reach Google", code="network") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400:
            error = str(payload.get("error") or "")
            if error == "invalid_grant":
                raise DriveAuthError("Доступ к Google Drive отозван или истёк — подключите аккаунт заново",
                                     "Google Drive access was revoked or expired — connect the account again",
                                     code="invalid_grant", status=response.status_code)
            if error in ("invalid_client", "unauthorized_client"):
                raise DriveAuthError("Google не принял Client ID / Client secret — проверьте их",
                                     "Google rejected the Client ID / Client secret — check them",
                                     code=error, status=response.status_code)
            raise DriveError(f"Google отказал в выдаче токена ({error or response.status_code})",
                             f"Google refused the token ({error or response.status_code})", status=response.status_code)
        return payload if isinstance(payload, dict) else {}

    def access_token(self, *, force: bool = False) -> str:
        with self._lock:
            token, expires = self._access
            if token and not force and time.time() < expires:
                return token
        refresh = self.store.get("google_refresh_token")
        if not refresh:
            raise DriveAuthError("Google Drive не подключён", "Google Drive is not connected", code="not_connected")
        cid, secret, _ = self.client()
        if not cid:
            raise DriveAuthError("Не указан OAuth Client ID", "No OAuth Client ID", code="no_client")
        data = {"client_id": cid, "refresh_token": refresh, "grant_type": "refresh_token"}
        if secret:
            data["client_secret"] = secret
        try:
            payload = self._token_request(data)
        except DriveAuthError as exc:
            if exc.code == "invalid_grant" and self.store.get("google_refresh_token") == refresh:
                # r29c: the grant is gone for good (revoked in the Google account, password change, 6 months
                # unused): forget it so Settings → Sync shows «Connect» again instead of a «connected» account
                # whose every backup fails, and the scheduler stops retrying a dead token.
                self.store.delete("google_refresh_token")
                self.store.delete("google_scopes")
            raise
        token = str(payload.get("access_token") or "")
        if not token:
            raise DriveError("Google не выдал токен доступа", "Google issued no access token")
        self._remember_scopes(payload)
        with self._lock:
            self._access = (token, time.time() + float(payload.get("expires_in") or 3000) - 60)
        return token

    def account_info(self) -> Dict[str, Any]:
        token = self.access_token()
        try:
            with self.http() as client:
                response = client.get(API + "/about", params={"fields": "user(displayName,emailAddress),storageQuota"},
                                      headers={"Authorization": "Bearer " + token})
        except httpx.HTTPError as exc:
            raise DriveError("Нет связи с Google Drive", "Cannot reach Google Drive", code="network") from exc
        if response.status_code >= 400:
            return self._userinfo(token)
        data = response.json()
        user = data.get("user") or {}
        quota = data.get("storageQuota") or {}

        def num(value: Any) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0
        return {"email": str(user.get("emailAddress") or "")[:200], "name": str(user.get("displayName") or "")[:200],
                "quota_limit": num(quota.get("limit")), "quota_usage": num(quota.get("usage"))}

    def _userinfo(self, token: str) -> Dict[str, Any]:
        """Round 30: the account of a token without Drive scopes (Gmail / Calendar only) — OpenID userinfo."""
        try:
            with self.http() as client:
                response = client.get(USERINFO_URL, headers={"Authorization": "Bearer " + token})
            data = response.json() if response.status_code < 400 else {}
        except (httpx.HTTPError, ValueError):
            return {}
        if not isinstance(data, dict) or not data.get("email"):
            return {}
        return {"email": str(data.get("email") or "")[:200], "name": str(data.get("name") or "")[:200],
                "quota_limit": 0, "quota_usage": 0}

    def disconnect(self, *, revoke: bool = True) -> None:
        refresh = self.store.get("google_refresh_token")
        self.store.delete("google_refresh_token")
        self.store.delete("google_scopes")
        with self._lock:
            self._access = ("", 0.0)
        if revoke and refresh:
            try:
                with self.http() as client:
                    client.post(REVOKE_URL, data={"token": refresh})
            except httpx.HTTPError:
                pass


class DriveTarget:
    """Backups stored in Google Drive: the hidden appDataFolder or a visible «MemuAI Backups» folder."""

    kind = "drive"

    def __init__(self, auth: DriveAuth, *, space: str = "appdata", folder_id_cache: Dict[str, str] | None = None,
                 throttle_kbps: int = 0, cancel: threading.Event | None = None) -> None:
        self.auth = auth
        self.space = "folder" if space == "folder" else "appdata"
        self.throttle = max(0, int(throttle_kbps or 0)) * 1024
        self._names: Optional[Dict[str, Dict[str, Any]]] = None
        self._folder_cache = folder_id_cache if folder_id_cache is not None else {}
        self.cancel = cancel or threading.Event()

    def describe(self) -> str:
        return "Google Drive" + (" · " + VISIBLE_FOLDER if self.space == "folder" else "")

    # ------------------------------------------------------------- requests
    def _request(self, method: str, url: str, *, retry_auth: bool = True, **kwargs: Any) -> httpx.Response:
        last: Optional[Exception] = None
        for attempt in range(RETRIES):
            if self.cancel.is_set():
                raise DriveError("Операция отменена", "Cancelled", code="cancelled")
            headers = dict(kwargs.pop("headers", {}) or {})
            headers["Authorization"] = "Bearer " + self.auth.access_token()
            try:
                with self.auth.http() as client:
                    response = client.request(method, url, headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                last = exc
                time.sleep(min(8.0, 0.5 * 2 ** attempt))
                kwargs["headers"] = {k: v for k, v in headers.items() if k != "Authorization"}
                continue
            kwargs["headers"] = {k: v for k, v in headers.items() if k != "Authorization"}
            if response.status_code == 401 and retry_auth:
                last = DriveAuthError("Токен Google истёк", "Google token expired")
                self.auth.access_token(force=True)
                continue
            if response.status_code in (429, 500, 502, 503, 504) or (
                    response.status_code == 403 and _reason(response) in ("rateLimitExceeded", "userRateLimitExceeded")):
                last = DriveError(f"Google Drive временно недоступен ({response.status_code})",
                                  f"Google Drive is temporarily unavailable ({response.status_code})",
                                  status=response.status_code)
                time.sleep(min(16.0, 0.75 * 2 ** attempt))
                continue
            return response
        if isinstance(last, DriveError):
            raise last
        raise DriveError("Нет связи с Google Drive", "Cannot reach Google Drive", code="network")

    def _check(self, response: httpx.Response, what_ru: str, what_en: str) -> httpx.Response:
        if response.status_code < 400:
            return response
        reason = _reason(response)
        if reason == "storageQuotaExceeded":
            raise DriveError("На Google Диске закончилось место", "Your Google Drive is full", code="quota",
                             status=response.status_code)
        if response.status_code == 403 and reason in ("insufficientPermissions", "insufficientScopes"):
            raise DriveAuthError("У MemuAI нет нужного доступа к Google Drive — подключите аккаунт заново",
                                 "MemuAI lacks the required Google Drive access — connect the account again",
                                 code="scope", status=403)
        if response.status_code == 404:
            raise DriveError(f"{what_ru}: файл не найден в Google Drive", f"{what_en}: file not found in Google Drive",
                             code="not_found", status=404)
        raise DriveError(f"{what_ru}: Google Drive ответил {response.status_code}",
                         f"{what_en}: Google Drive answered {response.status_code}", status=response.status_code)

    # -------------------------------------------------------------- folder
    def _parent(self) -> str:
        if self.space == "appdata":
            return "appDataFolder"
        cached = self._folder_cache.get("folder_id")
        if cached:
            return cached
        query = f"name = '{VISIBLE_FOLDER}' and mimeType = '{FOLDER_MIME}' and trashed = false"
        response = self._check(self._request("GET", API + "/files", params={
            "q": query, "fields": "files(id,name)", "pageSize": 10, "spaces": "drive"}), "Папка", "Folder")
        files = response.json().get("files") or []
        if files:
            folder_id = str(files[0]["id"])
        else:
            created = self._check(self._request("POST", API + "/files", params={"fields": "id"}, json={
                "name": VISIBLE_FOLDER, "mimeType": FOLDER_MIME,
                "description": "MemuAI backups (Settings → Sync)"}), "Папка", "Folder")
            folder_id = str(created.json()["id"])
        self._folder_cache["folder_id"] = folder_id
        return folder_id

    # ---------------------------------------------------------------- api
    def list(self, *, refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        if self._names is not None and not refresh:
            return dict(self._names)
        parent = self._parent()
        params: Dict[str, Any] = {"fields": "nextPageToken,files(id,name,size,modifiedTime)", "pageSize": 1000,
                                  "q": f"'{parent}' in parents and trashed = false and name contains 'memuai-'"}
        if self.space == "appdata":
            params["spaces"] = "appDataFolder"
        names: Dict[str, Dict[str, Any]] = {}
        while True:
            response = self._check(self._request("GET", API + "/files", params=params), "Список копий", "Backup list")
            data = response.json()
            for row in data.get("files") or []:
                name = str(row.get("name") or "")
                if not name:
                    continue
                current = {"id": str(row.get("id")), "size": int(row.get("size") or 0),
                           "modified": str(row.get("modifiedTime") or "")}
                # Two files with one name (an interrupted overwrite): keep the newest.
                if name not in names or current["modified"] > names[name]["modified"]:
                    names[name] = current
            token = data.get("nextPageToken")
            if not token:
                break
            params["pageToken"] = token
        self._names = names
        return dict(names)

    def _id(self, name: str) -> str:
        names = self.list()
        if name not in names:
            names = self.list(refresh=True)
        if name not in names:
            raise DriveError(f"Файл {name} не найден в Google Drive", f"File {name} is not in Google Drive", code="not_found",
                             status=404)
        return names[name]["id"]

    def _remember(self, name: str, row: Dict[str, Any], size: int) -> None:
        if self._names is None:
            self._names = {}
        self._names[name] = {"id": str(row.get("id") or ""), "size": int(row.get("size") or size),
                             "modified": str(row.get("modifiedTime") or "")}

    def put_bytes(self, name: str, data: bytes) -> None:
        existing = (self._names or {}).get(name)
        if existing is None and self._names is None:
            existing = self.list().get(name)
        if existing:
            response = self._check(self._request(
                "PATCH", f"{UPLOAD_API}/files/{existing['id']}", params={"uploadType": "media", "fields": "id,size,modifiedTime"},
                content=data, headers={"Content-Type": "application/octet-stream"}), "Загрузка", "Upload")
            self._remember(name, response.json(), len(data))
            return
        boundary = "memuai" + secrets.token_hex(12)
        meta = json.dumps({"name": name, "parents": [self._parent()]}).encode("utf-8")
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n").encode("ascii") + meta + \
            f"\r\n--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n".encode("ascii") + data + \
            f"\r\n--{boundary}--".encode("ascii")
        response = self._check(self._request(
            "POST", UPLOAD_API + "/files", params={"uploadType": "multipart", "fields": "id,size,modifiedTime"},
            content=body, headers={"Content-Type": f"multipart/related; boundary={boundary}"}), "Загрузка", "Upload")
        self._remember(name, response.json(), len(data))

    def put_file(self, name: str, path: Path, progress: Callable[[int], None] | None = None) -> None:
        size = Path(path).stat().st_size
        if size <= SIMPLE_LIMIT:
            self.put_bytes(name, Path(path).read_bytes())
            if progress:
                progress(size)
            return
        start = self._check(self._request(
            "POST", UPLOAD_API + "/files", params={"uploadType": "resumable", "fields": "id,size,modifiedTime"},
            json={"name": name, "parents": [self._parent()]},
            headers={"X-Upload-Content-Type": "application/octet-stream", "X-Upload-Content-Length": str(size)}),
            "Загрузка", "Upload")
        session = start.headers.get("location") or start.headers.get("Location")
        if not session:
            raise DriveError("Google Drive не открыл сеанс загрузки", "Google Drive did not open an upload session")
        offset = 0
        failures = 0
        with open(path, "rb") as reader:
            while offset < size:
                if self.cancel.is_set():
                    raise DriveError("Операция отменена", "Cancelled", code="cancelled")
                reader.seek(offset)
                chunk = reader.read(CHUNK)
                end = offset + len(chunk) - 1
                began = time.monotonic()
                try:
                    with self.auth.http() as client:
                        response = client.put(session, content=chunk, headers={
                            "Content-Length": str(len(chunk)), "Content-Range": f"bytes {offset}-{end}/{size}"})
                except httpx.HTTPError:
                    response = None
                if response is not None and response.status_code in (200, 201):
                    self._remember(name, response.json(), size)
                    if progress:
                        progress(size - offset)
                    return
                if response is not None and response.status_code == 308:
                    received = _range_end(response.headers.get("range") or response.headers.get("Range"))
                    if progress:
                        progress(received - offset)
                    offset = received
                    failures = 0
                    self._throttle(len(chunk), began)
                    continue
                if response is not None and 400 <= response.status_code < 500 and response.status_code not in (408, 429):
                    self._check(response, "Загрузка", "Upload")
                failures += 1
                if failures > RETRIES:
                    raise DriveError("Загрузка в Google Drive прервалась", "The upload to Google Drive was interrupted",
                                     code="network")
                time.sleep(min(16.0, 0.75 * 2 ** failures))
                # Ask how much Google already has, then continue from there.
                try:
                    with self.auth.http() as client:
                        status = client.put(session, content=b"", headers={"Content-Range": f"bytes */{size}"})
                    if status.status_code in (200, 201):
                        self._remember(name, status.json(), size)
                        return
                    if status.status_code == 308:
                        received = _range_end(status.headers.get("range") or status.headers.get("Range"))
                        if progress and received > offset:
                            progress(received - offset)
                        offset = received
                except httpx.HTTPError:
                    pass
        raise DriveError("Загрузка в Google Drive не завершилась", "The upload to Google Drive did not finish")

    def _throttle(self, sent: int, began: float) -> None:
        if not self.throttle:
            return
        wanted = sent / self.throttle
        spent = time.monotonic() - began
        if wanted > spent:
            time.sleep(min(60.0, wanted - spent))

    def get_bytes(self, name: str) -> bytes:
        response = self._check(self._request("GET", f"{API}/files/{self._id(name)}", params={"alt": "media"}),
                               "Скачивание", "Download")
        return response.content

    def get_file(self, name: str, dest: Path, progress: Callable[[int], None] | None = None) -> None:
        file_id = self._id(name)
        for attempt in range(RETRIES):
            headers = {"Authorization": "Bearer " + self.auth.access_token(force=attempt > 0)}
            try:
                with self.auth.http() as client:
                    with client.stream("GET", f"{API}/files/{file_id}", params={"alt": "media"}, headers=headers) as response:
                        if response.status_code >= 400:
                            response.read()
                            if response.status_code in (401, 429, 500, 502, 503, 504) and attempt < RETRIES - 1:
                                time.sleep(min(8.0, 0.5 * 2 ** attempt))
                                continue
                            self._check(response, "Скачивание", "Download")
                        with open(dest, "wb") as writer:
                            for chunk in response.iter_bytes(1024 * 1024):
                                if self.cancel.is_set():
                                    raise DriveError("Операция отменена", "Cancelled", code="cancelled")
                                writer.write(chunk)
                                if progress:
                                    progress(len(chunk))
                        return
            except httpx.HTTPError:
                time.sleep(min(8.0, 0.5 * 2 ** attempt))
        raise DriveError("Не удалось скачать файл из Google Drive", "Could not download the file from Google Drive",
                         code="network")

    def delete(self, name: str) -> None:
        try:
            file_id = self._id(name)
        except DriveError as exc:
            if exc.code == "not_found":
                return
            raise
        response = self._request("DELETE", f"{API}/files/{file_id}")
        if response.status_code not in (200, 204, 404):
            self._check(response, "Удаление", "Delete")
        if self._names is not None:
            self._names.pop(name, None)


def _reason(response: httpx.Response) -> str:
    try:
        error = response.json().get("error") or {}
        errors = error.get("errors") or [{}]
        return str(errors[0].get("reason") or error.get("status") or "")
    except Exception:
        return ""


def _range_end(header: Optional[str]) -> int:
    """``Range: bytes=0-1048575`` → 1048576 (bytes Google already stored)."""
    if not header or "-" not in header:
        return 0
    try:
        return int(header.rsplit("-", 1)[1]) + 1
    except ValueError:
        return 0
