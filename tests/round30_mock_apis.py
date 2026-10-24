"""Round 30: an in-memory fake of Google OAuth, Gmail, Calendar, Drive and Notion for tests (no network).

``FakeApis().handler`` is an ``httpx.MockTransport`` handler; every request is recorded in ``.calls`` as
``(method, url-without-query, parsed-json-or-None)``.  ``install(fake)`` routes ``app.integrations`` and the Google
account (``app.gdrive_sync_drive``) through it and returns a function that undoes the patch.
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

G = "https://www.googleapis.com/auth/"


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


class FakeApis:
    def __init__(self) -> None:
        self.calls: List[Tuple[str, str, Any]] = []
        self.access = "ya29.fake-access"
        self.refresh = "1//fake-refresh"
        self.granted = {"openid", G + "userinfo.email", G + "gmail.readonly", G + "gmail.compose",
                        G + "calendar.readonly", G + "calendar.events", G + "drive.readonly", G + "drive.file"}
        self.notion_token = "ntn_" + "a" * 40
        self.revoked: List[str] = []
        self.messages = {
            "m1": {"id": "m1", "threadId": "t1", "labelIds": ["INBOX", "UNREAD"], "snippet": "Отчёт за квартал готов",
                   "from": "Анна Петрова <anna@example.com>", "to": "me@example.com", "subject": "Квартальный отчёт",
                   "date": "Thu, 8 Oct 2026 10:00:00 +0300", "body": "Привет! Отчёт за третий квартал готов, лежит на Диске: «Отчёт Q3».",
                   "message_id": "<m1@example.com>"},
            "m2": {"id": "m2", "threadId": "t2", "labelIds": ["INBOX"], "snippet": "Встреча в пятницу",
                   "from": "Борис <boris@example.com>", "to": "me@example.com", "subject": "Встреча",
                   "date": "Fri, 9 Oct 2026 09:00:00 +0300", "body": "Давай встретимся в пятницу в 15:00.",
                   "message_id": "<m2@example.com>"},
        }
        self.events = [{"id": "e1", "summary": "Стендап", "start": {"dateTime": "2026-10-12T10:00:00+03:00"},
                        "end": {"dateTime": "2026-10-12T10:15:00+03:00"}, "location": "Zoom"}]
        self.files = {"f1": {"id": "f1", "name": "Отчёт Q3", "mimeType": "application/vnd.google-apps.document",
                             "modifiedTime": "2026-10-08T10:00:00Z", "webViewLink": "https://docs.google.com/document/d/f1"}}
        self.pages = {"11111111111111111111111111111111": {"title": "Планы на неделю", "blocks": [
            ("heading_2", "Понедельник"), ("to_do", "Созвон с командой"), ("paragraph", "Подготовить отчёт Q3")]}}
        self.sent: List[Dict[str, Any]] = []
        self.drafts: List[Dict[str, Any]] = []
        self.created_pages: List[Dict[str, Any]] = []
        self.appended: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _json(request: httpx.Request) -> Any:
        try:
            return json.loads(request.content.decode("utf-8")) if request.content else None
        except ValueError:
            return None

    def _msg(self, m: Dict[str, Any], full: bool) -> Dict[str, Any]:
        headers = [{"name": "From", "value": m["from"]}, {"name": "To", "value": m["to"]},
                   {"name": "Subject", "value": m["subject"]}, {"name": "Date", "value": m["date"]},
                   {"name": "Message-ID", "value": m["message_id"]}]
        payload: Dict[str, Any] = {"mimeType": "multipart/alternative", "headers": headers}
        if full:
            payload["parts"] = [{"mimeType": "text/plain", "body": {"data": _b64(m["body"]), "size": len(m["body"])}},
                                {"mimeType": "text/html", "body": {"data": _b64("<p>" + m["body"] + "</p>")}}]
        return {"id": m["id"], "threadId": m["threadId"], "labelIds": m["labelIds"], "snippet": m["snippet"], "payload": payload}

    def _page(self, pid: str) -> Dict[str, Any]:
        page = self.pages[pid]
        return {"object": "page", "id": pid, "url": "https://www.notion.so/" + pid, "last_edited_time": "2026-10-09T10:00:00Z",
                "properties": {"title": {"type": "title", "title": [{"plain_text": page["title"]}]}}}

    # ------------------------------------------------------------------ handler
    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        body = self._json(request)
        self.calls.append((request.method, url, body))
        q = dict(request.url.params)
        auth = request.headers.get("authorization", "")
        # ---- Google OAuth
        if url == "https://oauth2.googleapis.com/token":
            form = dict(x.split("=", 1) for x in request.content.decode().split("&") if "=" in x)
            if form.get("grant_type") == "refresh_token" and form.get("refresh_token", "").replace("%2F", "/") not in (self.refresh, "1//seeded-refresh-token"):
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": self.access, "expires_in": 3599, "scope": " ".join(sorted(self.granted)),
                                             "refresh_token": self.refresh})
        if url == "https://oauth2.googleapis.com/revoke":
            self.revoked.append("google")
            return httpx.Response(200, json={})
        if url.startswith("https://openidconnect.googleapis.com/v1/userinfo"):
            return httpx.Response(200, json={"email": "me@example.com", "name": "Me"})
        if url.startswith(("https://gmail.googleapis.com", "https://www.googleapis.com")) and auth != "Bearer " + self.access:
            return httpx.Response(401, json={"error": {"code": 401, "message": "Invalid Credentials", "status": "UNAUTHENTICATED"}})
        # ---- Gmail
        gm = "https://gmail.googleapis.com/gmail/v1/users/me"
        if url == gm + "/profile":
            return httpx.Response(200, json={"emailAddress": "me@example.com", "messagesTotal": 2})
        if url == gm + "/messages" and request.method == "GET":
            query = (q.get("q") or "").lower()
            ids = [m for m in self.messages.values()
                   if not query or any(w in (m["from"] + m["subject"] + m["body"]).lower()
                                       for w in re.findall(r"[\w@.]+", query.replace("from:", "")) if len(w) > 2)]
            ids = ids or list(self.messages.values())
            return httpx.Response(200, json={"messages": [{"id": m["id"], "threadId": m["threadId"]} for m in ids], "resultSizeEstimate": len(ids)})
        m = re.match(re.escape(gm) + r"/messages/([^/]+)$", url)
        if m and request.method == "GET":
            row = self.messages.get(m.group(1))
            if not row:
                return httpx.Response(404, json={"error": {"code": 404, "message": "Not Found"}})
            return httpx.Response(200, json=self._msg(row, q.get("format") == "full"))
        if url == gm + "/drafts" and request.method == "POST":
            self.drafts.append(body)
            return httpx.Response(200, json={"id": "d%d" % len(self.drafts), "message": {"id": "dm%d" % len(self.drafts)}})
        if url in (gm + "/messages/send", gm + "/drafts/send"):
            self.sent.append(body)
            return httpx.Response(200, json={"id": "s%d" % len(self.sent), "threadId": "ts"})
        # ---- Calendar
        cal = "https://www.googleapis.com/calendar/v3"
        if url == cal + "/users/me/calendarList":
            return httpx.Response(200, json={"items": [{"id": "me@example.com", "summary": "me@example.com", "primary": True}]})
        cm = re.match(re.escape(cal) + r"/calendars/([^/]+)/events(?:/([^/]+))?$", url)
        if cm:
            if request.method == "GET":
                return httpx.Response(200, json={"items": self.events})
            if request.method == "POST":
                row = dict(body or {}, id="e%d" % (len(self.events) + 1), htmlLink="https://calendar.google.com/e", status="confirmed")
                self.events.append(row)
                return httpx.Response(200, json=row)
            if request.method == "PATCH":
                for row in self.events:
                    if row["id"] == cm.group(2):
                        row.update(body or {})
                        return httpx.Response(200, json=dict(row, htmlLink="https://calendar.google.com/e", status="confirmed"))
                return httpx.Response(404, json={"error": {"code": 404, "message": "Not Found"}})
            if request.method == "DELETE":
                self.events = [row for row in self.events if row["id"] != cm.group(2)]
                return httpx.Response(204)
        # ---- Drive
        drv = "https://www.googleapis.com/drive/v3"
        if url == drv + "/files" and request.method == "GET":
            return httpx.Response(200, json={"files": list(self.files.values())})
        if url == drv + "/about":
            return httpx.Response(200, json={"user": {"emailAddress": "me@example.com", "displayName": "Me"}})
        dm = re.match(re.escape(drv) + r"/files/([^/]+)(/export)?$", url)
        if dm:
            row = self.files.get(dm.group(1))
            if not row:
                return httpx.Response(404, json={"error": {"code": 404, "message": "File not found"}})
            if dm.group(2):
                return httpx.Response(200, content="Отчёт Q3: выручка выросла на 12%.".encode("utf-8"), headers={"content-type": "text/plain"})
            return httpx.Response(200, json=row)
        if url == "https://www.googleapis.com/upload/drive/v3/files" and request.method == "POST":
            fid = "f%d" % (len(self.files) + 1)
            text = request.content.decode("utf-8", "replace")
            name = re.search(r'"name":\s*"([^"]+)"', text)
            self.files[fid] = {"id": fid, "name": name.group(1) if name else "file", "mimeType": "text/plain"}
            return httpx.Response(200, json={"id": fid, "name": self.files[fid]["name"], "webViewLink": "https://drive.google.com/" + fid})
        # ---- Notion
        if url.startswith("https://api.notion.com/v1"):
            path = url[len("https://api.notion.com/v1"):]
            if path == "/oauth/revoke":
                self.revoked.append("notion")
                return httpx.Response(200, json={})
            if path == "/oauth/token":
                return httpx.Response(200, json={"access_token": self.notion_token, "refresh_token": "nrt_x", "bot_id": "b",
                                                 "workspace_id": "w", "workspace_name": "Мой Notion"})
            if auth != "Bearer " + self.notion_token:
                return httpx.Response(401, json={"object": "error", "status": 401, "code": "unauthorized", "message": "API token is invalid."})
            if path == "/users/me":
                return httpx.Response(200, json={"object": "user", "type": "bot", "bot": {"workspace_name": "Мой Notion"}})
            if path == "/search":
                return httpx.Response(200, json={"results": [self._page(pid) for pid in self.pages], "has_more": False})
            pm = re.match(r"/pages/([0-9a-f-]+)$", path)
            if pm:
                pid = pm.group(1).replace("-", "")
                if pid not in self.pages:
                    return httpx.Response(404, json={"object": "error", "status": 404, "code": "object_not_found", "message": "not shared"})
                return httpx.Response(200, json=self._page(pid))
            bm = re.match(r"/blocks/([0-9a-f-]+)/children$", path)
            if bm:
                pid = bm.group(1).replace("-", "")
                if request.method == "PATCH":
                    self.appended.append({"page": pid, "children": (body or {}).get("children")})
                    return httpx.Response(200, json={"results": []})
                blocks = [{"id": "b%d" % i, "type": kind, "has_children": False, kind: {"rich_text": [{"plain_text": text}]}}
                          for i, (kind, text) in enumerate(self.pages.get(pid, {}).get("blocks", []))]
                return httpx.Response(200, json={"results": blocks, "has_more": False})
            if re.match(r"/databases/[0-9a-f-]+(/query)?$", path):
                return httpx.Response(404, json={"object": "error", "status": 404, "code": "object_not_found", "message": "no database"})
            if path == "/pages" and request.method == "POST":
                self.created_pages.append(body)
                return httpx.Response(200, json={"id": "22222222-2222-2222-2222-222222222222", "url": "https://www.notion.so/new"})
        return httpx.Response(404, json={"error": {"code": 404, "message": "fake: no route " + url}})


def install(fake: FakeApis) -> Callable[[], None]:
    """Route app.integrations and the shared Google account through ``fake``; returns the undo function."""
    from app import gdrive_sync_drive, integrations
    old_client, old_factory = integrations._client, gdrive_sync_drive._client_factory
    integrations._client = lambda: httpx.Client(transport=httpx.MockTransport(fake.handler), follow_redirects=True)
    gdrive_sync_drive._client_factory = lambda transport=None: httpx.Client(transport=httpx.MockTransport(fake.handler))

    def undo() -> None:
        integrations._client = old_client
        gdrive_sync_drive._client_factory = old_factory
    return undo
