"""Round 30: ONE Google account for everything in MemuAI — Gmail, Calendar, Drive, backups and the Drive picker.

The account is the OAuth grant of «Синхронизация» (:mod:`app.gdrive_sync_drive`):
the installed-app loopback flow with PKCE on ``127.0.0.1:<random port>``, the
refresh token in the sync :class:`SecretStore` (DPAPI on Windows), the access
token only in memory.  Settings → Integrations adds *services* to that grant:

* the user ticks Gmail / Calendar / Drive; only the scopes of the ticked
  services are requested (plus ``openid email`` to show who is signed in);
* a service switched on later asks Google again for just its scopes —
  ``include_granted_scopes`` keeps everything granted before (incremental
  authorization), so backups and the other services keep working;
* «Disconnect» revokes the token at Google and forgets it (backups and the
  picker are disconnected too — they share the account; the UI says so).

The choice of services (not a secret) lives in ``integrations.json`` under
``google.services``.  A service is *ready* when it is chosen **and** its scopes
are in the token.  Tokens never leave this module and :mod:`app.integrations`.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

G = "https://www.googleapis.com/auth/"
BASE_SCOPES = ("openid", G + "userinfo.email")

MAIL = "https://mail.google.com/"

#: service → title, the scopes to request, ``need`` (groups: one scope of each must be granted for the service to
#: work at all) and ``full`` (groups for every tool of the service — Round 33).  A token from an older MemuAI
#: (Round 30: gmail.readonly + gmail.compose, drive.file, calendar.events) is still «ready»; the rest of the tools
#: then ask to «Expand access», which re-runs the consent for ``request`` (incremental, nothing is lost).
#: ``api`` / ``api_title`` — the Google Cloud API that must be enabled in the user's project for this service.
SERVICES: Dict[str, Dict[str, Any]] = {
    "gmail": {
        "title": "Gmail",
        # mail.google.com: permanent delete (messages.batchDelete / threads.delete) accepts only this scope;
        # gmail.settings.basic: filters and the vacation responder (not covered by mail.google.com).
        "request": (MAIL, G + "gmail.settings.basic"),
        "need": ({G + "gmail.readonly", G + "gmail.modify", MAIL},
                 {G + "gmail.compose", G + "gmail.modify", MAIL}),
        "full": ({MAIL}, {G + "gmail.settings.basic"}),
        "api": "gmail.googleapis.com", "api_title": "Gmail API",
    },
    "calendar": {
        "title": "Google Calendar",
        "request": (G + "calendar",),
        "need": ({G + "calendar.readonly", G + "calendar"}, {G + "calendar.events", G + "calendar"}),
        "full": ({G + "calendar"},),
        "api": "calendar-json.googleapis.com", "api_title": "Google Calendar API",
    },
    "drive": {
        "title": "Google Drive",
        # drive (not drive.file): rename / move / delete / share files the user made, not only MemuAI's own.
        "request": (G + "drive",),
        "need": ({G + "drive.readonly", G + "drive"}, {G + "drive.file", G + "drive"}),
        "full": ({G + "drive"},),
        "api": "drive.googleapis.com", "api_title": "Google Drive API",
    },
    "docs": {
        "title": "Google Docs",
        "request": (G + "documents",),
        "need": ({G + "documents"},),
        "full": ({G + "documents"},),
        "api": "docs.googleapis.com", "api_title": "Google Docs API",
    },
    "sheets": {
        "title": "Google Sheets",
        "request": (G + "spreadsheets",),
        "need": ({G + "spreadsheets"},),
        "full": ({G + "spreadsheets"},),
        "api": "sheets.googleapis.com", "api_title": "Google Sheets API",
    },
    "tasks": {
        "title": "Google Tasks",
        "request": (G + "tasks",),
        "need": ({G + "tasks"},),
        "full": ({G + "tasks"},),
        "api": "tasks.googleapis.com", "api_title": "Google Tasks API",
    },
    "contacts": {
        "title": "Google Contacts",
        "request": (G + "contacts",),
        "need": ({G + "contacts"},),
        "full": ({G + "contacts"},),
        "api": "people.googleapis.com", "api_title": "People API",
    },
}
ORDER = ("gmail", "calendar", "drive", "docs", "sheets", "tasks", "contacts")
#: The integration ids the rest of MemuAI already uses (panels, prompts, tool prefixes).
INTEGRATION_ID = {"gmail": "gmail", "calendar": "google_calendar", "drive": "google_drive", "docs": "google_docs",
                  "sheets": "google_sheets", "tasks": "google_tasks", "contacts": "google_contacts"}
#: Every API MemuAI uses (the «enable all» link of the setup wizard).
ALL_APIS = tuple(SERVICES[name]["api"] for name in ORDER)
CONSOLE = "https://console.cloud.google.com"

_lock = threading.Lock()
log = logging.getLogger("memuai.google")


class GoogleAccountError(RuntimeError):
    """A human message (ru) + its English twin and a code for the interface."""

    def __init__(self, ru: str, en: str = "", *, code: str = "") -> None:
        super().__init__(ru)
        self.ru, self.en, self.code = ru, en or ru, code


# --------------------------------------------------------------------------- account plumbing
def sync_service():
    """The process-wide SyncService (MemuAI or Proxy): its ``auth`` is the shared Google account."""
    from . import gdrive_sync
    return gdrive_sync.service()


def auth():
    return sync_service().auth


def _prefs() -> Dict[str, Any]:
    from . import integrations
    data = integrations._load_store().get("google") or {}
    return data if isinstance(data, dict) else {}


def chosen() -> List[str]:
    services = _prefs().get("services")
    if not isinstance(services, list):
        return []
    return [name for name in ORDER if name in services]


def _save_chosen(names: List[str]) -> None:
    from . import integrations
    with integrations._store_transaction():
        store = integrations._load_store()
        current = dict(store.get("google") or {})
        current["services"] = [name for name in ORDER if name in set(names)]
        store["google"] = current
        integrations._save_store(store)


def granted(name: str, scopes: Optional[set] = None) -> bool:
    scopes = auth().granted_scopes() if scopes is None else scopes
    return all(group & scopes for group in SERVICES[name]["need"])


def full_access(name: str, scopes: Optional[set] = None) -> bool:
    """Round 33: the token covers every tool of the service (not only the Round 30 basics)."""
    scopes = auth().granted_scopes() if scopes is None else scopes
    return all(group & scopes for group in SERVICES[name].get("full", SERVICES[name]["need"]))


def project_number(client_id: str = "") -> str:
    """The Google Cloud project of an OAuth client: a Client ID is «<project number>-<hash>.apps.googleusercontent.com»."""
    cid = client_id
    if not cid:
        try:
            cid = auth().client()[0]
        except Exception:
            cid = ""
    head = str(cid or "").split("-", 1)[0]
    return head if head.isdigit() else ""


def enable_url(apis: Tuple[str, ...] = ALL_APIS, project: str = "") -> str:
    """Google Cloud Console's «Enable APIs» page for ``apis`` in the user's project (when it is known)."""
    project = project or project_number()
    url = CONSOLE + "/flows/enableapi?apiid=" + ",".join(apis)
    return url + ("&project=" + project if project else "")


def api_url(api: str, project: str = "") -> str:
    """The page of one API (its «Enable» button) in the user's project."""
    project = project or project_number()
    return CONSOLE + "/apis/library/" + api + ("?project=" + project if project else "")


def ready(name: str) -> bool:
    """Chosen by the user, signed in and the token carries the service's scopes."""
    _migrate_legacy()
    return name in chosen() and auth().connected() and granted(name)


def account() -> Dict[str, str]:
    try:
        info = (sync_service().state() or {}).get("account") or {}
    except Exception:
        info = {}
    return {"email": str(info.get("email") or ""), "name": str(info.get("name") or "")}


def client_info() -> Dict[str, Any]:
    svc = sync_service()
    cid, _secret, source = svc.auth.client()
    settings = svc.settings()
    from .oauth_config import client as configured
    from . import gdrive_sync_drive
    project = project_number(cid)
    return {"has_client": bool(cid), "source": source, "own_client_id": settings.get("client_id") or "",
            "own_has_secret": svc.store.has("client_secret"),
            "bundled": bool(gdrive_sync_drive.BUNDLED_CLIENT_ID or configured("google")[0]),
            # Round 33: the user's project (from the Client ID) → links straight into *their* console.
            "project": project if source == "user" or not source else "", "enable_all_url": enable_url(ALL_APIS, project if source == "user" else "")}


def status() -> Dict[str, Any]:
    _migrate_legacy()
    a = auth()
    connected = a.connected()
    scopes = a.granted_scopes() if connected else set()
    picked = chosen()
    services = []
    for name in ORDER:
        meta = SERVICES[name]
        has = connected and granted(name, scopes)
        full = connected and full_access(name, scopes)
        services.append({"id": name, "integration": INTEGRATION_ID[name], "title": meta["title"],
                         "enabled": name in picked, "granted": has, "ready": has and name in picked,
                         "full": full, "api": meta["api"], "api_title": meta["api_title"],
                         "api_url": api_url(meta["api"])})
    return {"connected": connected, "account": account() if connected else {"email": "", "name": ""},
            "services": services, "client": client_info(), "flow": a.flow_status(),
            "backups": connected and a.has_backup_scopes()}


# --------------------------------------------------------------------------- actions
def connect(names: List[str]) -> Dict[str, Any]:
    """Sign in (or add services): opens Google's consent page for the scopes of ``names`` only."""
    wanted = [name for name in ORDER if name in set(names or [])]
    if not wanted:
        raise GoogleAccountError("Отметьте хотя бы один сервис", "Tick at least one service", code="no_services")
    a = auth()
    if not a.client()[0]:
        raise GoogleAccountError("Сначала добавьте OAuth-клиент Google — мастер ниже покажет, где его взять",
                                 "Add a Google OAuth client first — the wizard below shows where to get it", code="no_client")
    _save_chosen(sorted(set(chosen()) | set(wanted)))
    scopes = a.granted_scopes() if a.connected() else set()
    # Round 33: «missing» = not every tool is covered (re-consent when the scopes of a service grew).
    missing = [name for name in wanted if not full_access(name, scopes)]
    if a.connected() and not missing:
        return {"state": "ready", "auth_url": ""}
    extra: Tuple[str, ...] = tuple(scope for name in (missing or wanted) for scope in SERVICES[name]["request"])
    return a.start_flow(extra_scopes=extra, purpose="integrations", base_scopes=BASE_SCOPES)


def set_enabled(name: str, enabled: bool) -> Dict[str, Any]:
    if name not in SERVICES:
        raise GoogleAccountError("Неизвестный сервис Google: %s" % name, "Unknown Google service: %s" % name, code="bad_service")
    if enabled:
        return connect([name])
    _save_chosen([n for n in chosen() if n != name])
    return {"state": "off", "auth_url": ""}


def cancel_flow() -> None:
    auth().cancel_flow()


def save_client(client_id: str, client_secret: str = "") -> Dict[str, Any]:
    """The user's own OAuth client — the same one Sync uses (one account, one client)."""
    cid = str(client_id or "").strip().strip('"').strip()
    if cid and not cid.endswith(".apps.googleusercontent.com"):
        raise GoogleAccountError("Client ID выглядит не так: он заканчивается на .apps.googleusercontent.com",
                                 "That is not a Client ID: it ends with .apps.googleusercontent.com", code="bad_client")
    updates: Dict[str, Any] = {"client_id": cid}
    secret = str(client_secret or "").strip()
    if secret or not cid:
        updates["client_secret"] = secret
    sync_service().save_settings(updates)
    return client_info()


def disconnect() -> None:
    """Revoke the token at Google and forget it — every Google feature of MemuAI is disconnected."""
    svc = sync_service()
    svc.auth.disconnect(revoke=True)
    try:
        svc._update_state(account=None)
    except Exception:
        pass
    _save_chosen([])
    from . import integrations
    for service in INTEGRATION_ID.values():
        integrations._tokens.pop(service, None)


def access_token(name: str, *, force: bool = False) -> str:
    """An access token for a ready service, or an error the model and the user both understand."""
    title = SERVICES[name]["title"]
    _migrate_legacy()
    a = auth()
    if not a.connected():
        raise GoogleAccountError(
            "%s не подключён. Откройте Настройки → Интеграции → Google и нажмите «Войти через Google»." % title,
            "%s is not connected. Open Settings → Integrations → Google and press «Sign in with Google»." % title,
            code="not_connected")
    if name not in chosen():
        raise GoogleAccountError("%s выключен в Настройки → Интеграции → Google." % title,
                                 "%s is turned off in Settings → Integrations → Google." % title, code="disabled")
    if not granted(name):
        raise GoogleAccountError(
            "%s: нужно разрешение Google — включите сервис ещё раз в Настройки → Интеграции → Google." % title,
            "%s needs Google's permission — switch the service on again in Settings → Integrations → Google." % title,
            code="need_consent")
    try:
        return a.access_token(force=force)
    except Exception as exc:  # DriveAuthError / DriveError carry ru/en
        ru = getattr(exc, "ru", "") or str(exc)
        en = getattr(exc, "en", "") or ru
        code = getattr(exc, "code", "") or "auth"
        if code == "invalid_grant":
            ru = "Доступ к Google отозван или истёк — войдите заново: Настройки → Интеграции → Google."
            en = "Google access was revoked or expired — sign in again: Settings → Integrations → Google."
        raise GoogleAccountError(ru, en, code=code) from exc


# --------------------------------------------------------------------------- legacy (≤ round 29)
_migrated = {"done": False}


def _migrate_legacy() -> None:
    """Old Integrations kept a Client ID / secret / refresh token per Drive and Calendar, typed by hand.

    When the shared account is not signed in yet, the first legacy grant becomes the account (its client becomes
    the user's own client, its scopes are recorded), so nothing stops working and there is one account, not two.
    The hand-typed copies are then removed from ``integrations.json``.
    """
    if _migrated["done"]:
        return
    with _lock:
        if _migrated["done"]:
            return
        _migrated["done"] = True
        try:
            from . import integrations
            store = integrations._load_store()
            legacy = [(name, store.get(name) or {}) for name in ("google_drive", "google_calendar")
                      if (store.get(name) or {}).get("refresh_token")]
            if not legacy:
                return
            svc = sync_service()
            if not svc.auth.connected():
                name, data = legacy[0]
                svc.save_settings({"client_id": str(data.get("client_id") or ""),
                                   "client_secret": str(data.get("client_secret") or "")})
                svc.store.set("google_refresh_token", str(data["refresh_token"]))
                scopes = set()
                services = []
                for service, row in legacy:
                    if row.get("refresh_token") == data["refresh_token"]:
                        scopes.add(G + ("drive" if service == "google_drive" else "calendar"))
                        services.append("drive" if service == "google_drive" else "calendar")
                svc.store.set("google_scopes", " ".join(sorted(scopes)))
                if data.get("account"):
                    svc._update_state(account={"email": str(data.get("account")), "name": ""})
                _save_chosen(sorted(set(chosen()) | set(services)))
            with integrations._store_transaction():
                store = integrations._load_store()
                for name, _row in legacy:
                    calendar_id = str((store.get(name) or {}).get("calendar_id") or "")
                    store.pop(name, None)
                    if calendar_id:
                        google = dict(store.get("google") or {})
                        google["calendar_id"] = calendar_id
                        store["google"] = google
                integrations._save_store(store)
        except Exception as exc:  # never break Settings or a tool call over an old file
            log.warning("google account: legacy integration keys were not migrated: %s", type(exc).__name__)


def default_calendar() -> str:
    return str(_prefs().get("calendar_id") or "").strip()
