"""Round 29 «Добавить с Google Диска»: the Drive picker (app/gdrive_picker.py) against a fake Google.

Offline: Google's token endpoint and Drive API v3 are an in-memory fake behind
``httpx.MockTransport``.  Covered: incremental authorization (the extra
``drive.readonly`` scope is asked only by the picker, through the real loopback
listener; the backup scopes stay), status states, listing queries (folders,
recent, shared, starred, search, type filters, paging), item normalisation
(Google types, shortcuts, too-big files), downloads and exports (Docs → DOCX,
Sheets → Markdown tables, Slides → PDF), size limits, token refresh on 401,
insufficient-scope / forbidden / not-found errors, the Integrations fallback,
thumbnail host check, the HTTP routes and the UI hooks.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r29-gdpick-")

import httpx  # noqa: E402

from app import gdrive_picker as gp  # noqa: E402
from app import gdrive_sync as core  # noqa: E402
from app import gdrive_sync_drive as drive  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DOC = "application/vnd.google-apps.document"
SHEET = "application/vnd.google-apps.spreadsheet"
SLIDES = "application/vnd.google-apps.presentation"
FORM = "application/vnd.google-apps.form"


def xlsx(rows: list[list[str]], name: str = "Budget") -> bytes:
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    cells = "".join(f'<row r="{r + 1}">' + "".join(f'<c t="inlineStr"><is><t>{v}</t></is></c>' for v in row) + "</row>"
                    for r, row in enumerate(rows))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/workbook.xml", f'<workbook {ns}><sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets></workbook>')
        zf.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Type="x" Target="worksheets/sheet1.xml"/></Relationships>')
        zf.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{cells}</sheetData></worksheet>")
    return buf.getvalue()


class FakeGoogle:
    """Token endpoint + the parts of Drive v3 the picker uses."""

    def __init__(self) -> None:
        self.challenge = ""
        self.access = "ya29.A1"
        self.refresh = "1//R1"
        self.granted = {drive.SCOPE_APPDATA, drive.SCOPE_FILE}
        self.asked_scope = ""
        self.expire_next = False
        self.requests: list[httpx.Request] = []
        self.lock = threading.Lock()
        self.files = {
            "fold1": {"name": "Projects", "mimeType": "application/vnd.google-apps.folder", "parents": ["root"]},
            "doc1": {"name": "Plan Q4", "mimeType": DOC, "parents": ["root"], "starred": True},
            "sheet1": {"name": "Budget", "mimeType": SHEET, "parents": ["fold1"]},
            "slides1": {"name": "Pitch", "mimeType": SLIDES, "parents": ["fold1"]},
            "pdf1": {"name": "report.pdf", "mimeType": "application/pdf", "parents": ["root"], "data": b"%PDF-1.4 tiny"},
            "img1": {"name": "photo.png", "mimeType": "image/png", "parents": ["root"], "data": b"\x89PNG....",
                     "thumbnailLink": "https://lh3.googleusercontent.com/abc=s220"},
            "big1": {"name": "video.mp4", "mimeType": "video/mp4", "parents": ["root"], "size": 50 * 1024 * 1024},
            "nosz": {"name": "stream.bin", "mimeType": "application/octet-stream", "parents": ["root"], "data": b"x" * (6 * 1024 * 1024),
                     "hide_size": True},
            "xl1": {"name": "Sales.xlsx", "mimeType": gp.XLSX, "parents": ["root"], "data": xlsx([["Region", "Sum"], ["North", "10"]], "Q1")},
            "form1": {"name": "Survey", "mimeType": FORM, "parents": ["root"]},
            "lock1": {"name": "secret.txt", "mimeType": "text/plain", "parents": ["root"], "data": b"no", "canDownload": False},
            "short1": {"name": "Plan shortcut", "mimeType": gp.SHORTCUT, "parents": ["root"],
                       "shortcutDetails": {"targetId": "doc1", "targetMimeType": DOC}},
            "shared1": {"name": "From a friend.txt", "mimeType": "text/plain", "parents": [], "shared": True, "data": b"hello",
                        "owner": "Friend"},
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.requests.append(request)
            return self._handle(request)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if str(url).startswith(drive.TOKEN_URL):
            form = dict(urllib.parse.parse_qsl(request.content.decode()))
            if form["grant_type"] == "authorization_code":
                expected = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).decode().rstrip("=")
                if form.get("code") != "CODE" or expected != self.challenge:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                # include_granted_scopes: the new token covers the old scopes plus the new ones.
                self.granted |= set(self.asked_scope.split())
                self.refresh = "1//R2"
                return httpx.Response(200, json={"access_token": self.access, "refresh_token": self.refresh,
                                                 "expires_in": 3599, "scope": " ".join(sorted(self.granted))})
            if form["refresh_token"] != self.refresh:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.access = f"ya29.A{len(self.requests)}"
            return httpx.Response(200, json={"access_token": self.access, "expires_in": 3599, "scope": " ".join(sorted(self.granted))})
        if url.host.endswith("googleusercontent.com"):
            return httpx.Response(200, content=b"JPEGDATA", headers={"content-type": "image/jpeg"})
        if request.headers.get("authorization") != "Bearer " + self.access:
            return httpx.Response(401, json={"error": {"code": 401}})
        if self.expire_next:
            self.expire_next = False
            return httpx.Response(401, json={"error": {"code": 401}})
        path = url.path
        if path == "/drive/v3/about":
            return httpx.Response(200, json={"user": {"emailAddress": "user@example.com", "displayName": "User"}})
        if not (self.granted & gp.READ_SCOPES):
            return httpx.Response(403, json={"error": {"code": 403, "message": "Request had insufficient authentication scopes.",
                                                       "errors": [{"reason": "insufficientPermissions"}]}})
        if path == "/drive/v3/files":
            return self._list(url)
        m = re.fullmatch(r"/drive/v3/files/([^/]+)(/export)?", path)
        if not m or m.group(1) not in self.files:
            return httpx.Response(404, json={"error": {"code": 404, "errors": [{"reason": "notFound"}]}})
        fid, row = m.group(1), self.files[m.group(1)]
        if m.group(2):
            mime = url.params["mimeType"]
            if row["mimeType"] == SHEET and mime == gp.XLSX:
                return httpx.Response(200, content=xlsx([["Item", "Cost"], ["Rent", "100"], ["Food | drinks", "40"]]))
            return httpx.Response(200, content=f"EXPORT {fid} as {mime}".encode())
        if url.params.get("alt") == "media":
            if row.get("canDownload") is False:
                return httpx.Response(403, json={"error": {"errors": [{"reason": "cannotDownloadFile"}]}})
            return httpx.Response(200, content=row.get("data", b""))
        return httpx.Response(200, json=self._meta(fid, row))

    def _meta(self, fid: str, row: dict) -> dict:
        meta = {"id": fid, "name": row["name"], "mimeType": row["mimeType"], "modifiedTime": "2026-10-01T10:00:00Z",
                "owners": [{"displayName": row.get("owner", "User"), "me": "owner" not in row}],
                "capabilities": {"canDownload": row.get("canDownload", True)}, "parents": row["parents"]}
        if "data" in row and not row.get("hide_size"):
            meta["size"] = str(len(row["data"]))
        if "size" in row:
            meta["size"] = str(row["size"])
        for key in ("thumbnailLink", "shortcutDetails"):
            if key in row:
                meta[key] = row[key]
        return meta

    def _list(self, url: httpx.URL) -> httpx.Response:
        q = url.params.get("q", "")
        rows = []
        for fid, row in self.files.items():
            parent = re.search(r"'([^']+)' in parents", q)
            if parent and parent.group(1) not in row["parents"]:
                continue
            if "sharedWithMe = true" in q and not row.get("shared"):
                continue
            if "starred = true" in q and not row.get("starred"):
                continue
            needle = re.search(r"name contains '((?:[^'\\]|\\.)*)'", q)
            if needle and needle.group(1).replace("\\'", "'").lower() not in row["name"].lower():
                continue
            if "mimeType != 'application/vnd.google-apps.folder'" in q and row["mimeType"].endswith("folder"):
                continue
            if "mimeType contains 'image/'" in q and not row["mimeType"].startswith("image/"):
                continue
            types = re.findall(r"mimeType = '([^']+)'", q)
            if types and row["mimeType"] not in types:
                continue
            rows.append(self._meta(fid, row))
        start = int(url.params.get("pageToken") or 0)
        body = {"files": rows[start:start + 4]}
        if start + 4 < len(rows):
            body["nextPageToken"] = str(start + 4)
        return httpx.Response(200, json=body)


class PickerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="gdpick-"))
        self.google = FakeGoogle()
        self.transport = httpx.MockTransport(self.google.handler)
        self.opened: list[str] = []
        self.svc = core.SyncService(self.tmp, transport=self.transport, open_browser=self.opened.append)
        self.svc.save_settings({"client_id": "cid.apps.googleusercontent.com", "client_secret": "GOCSPX-x"})
        self.picker = gp.Picker(lambda: self.svc, transport=self.transport)

    def connect(self, readonly: bool = True) -> None:
        self.svc.store.set("google_refresh_token", self.google.refresh)
        if readonly:
            self.google.granted.add(gp.SCOPE_READONLY)
            self.svc.store.set("google_scopes", " ".join(sorted(self.google.granted)))

    # ------------------------------------------------------------------ status / auth
    def test_status_states(self) -> None:
        bare = core.SyncService(self.tmp / "other", transport=self.transport)
        self.assertEqual(gp.Picker(lambda: bare, transport=self.transport).status()["state"], "no_client")
        self.assertEqual(self.picker.status()["state"], "not_connected")
        self.svc.store.set("google_refresh_token", self.google.refresh)   # connected for backups only (old token)
        status = self.picker.status()
        self.assertEqual((status["state"], status["source"]), ("need_consent", "sync"))
        self.connect()
        self.assertEqual(self.picker.status()["state"], "ready")
        self.assertNotIn("token", json.dumps(self.picker.status()).lower().replace("google_refresh", ""))

    def test_incremental_authorization_keeps_backup_scopes(self) -> None:
        self.svc.store.set("google_refresh_token", self.google.refresh)
        result = self.picker.authorize()
        url = result["auth_url"]
        self.assertEqual(self.opened, [url], "the default browser opens Google's page")
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        scopes = params["scope"].split()
        self.assertEqual(scopes, [drive.SCOPE_APPDATA, drive.SCOPE_FILE, gp.SCOPE_READONLY])
        self.assertEqual(params["include_granted_scopes"], "true")
        self.assertEqual(self.svc.auth.flow_status()["purpose"], "picker")
        self.google.challenge = params["code_challenge"]
        self.google.asked_scope = params["scope"]
        redirect = params["redirect_uri"]
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                f"{redirect}?code=CODE&state={urllib.parse.quote(params['state'])}", timeout=10) as page:
            self.assertIn(b"Google Drive", page.read())
        self.assertEqual(self.svc.auth.flow_status()["state"], "done")
        self.assertEqual(self.svc.auth.flow_status()["purpose"], "picker")
        self.assertIn(gp.SCOPE_READONLY, self.svc.auth.granted_scopes())
        self.assertIn(drive.SCOPE_APPDATA, self.svc.auth.granted_scopes())
        self.assertEqual(self.picker.status()["state"], "ready")
        # The sync flow itself still asks only for the backup scopes.
        sync_url = self.svc.auth.start_flow()["auth_url"]
        self.svc.auth.cancel_flow()
        sync_scopes = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(sync_url).query))["scope"].split()
        self.assertEqual(sync_scopes, [drive.SCOPE_APPDATA, drive.SCOPE_FILE])
        self.svc.auth.disconnect(revoke=False)
        self.assertEqual(self.svc.auth.granted_scopes(), set())
        self.assertEqual(self.svc.store.get("google_scopes"), "")

    def test_need_consent_error_and_no_client(self) -> None:
        self.svc.store.set("google_refresh_token", self.google.refresh)
        with self.assertRaises(gp.PickerError) as caught:
            self.picker.list()
        self.assertEqual(caught.exception.code, "need_consent")
        bare = core.SyncService(self.tmp / "o2", transport=self.transport)
        with self.assertRaises(gp.PickerError) as caught:
            gp.Picker(lambda: bare, transport=self.transport).authorize()
        self.assertEqual(caught.exception.code, "no_client")

    def test_scope_recorded_but_revoked_on_google_side(self) -> None:
        # The app believes it may read, Google says otherwise (403 insufficient scopes) → consent again.
        self.svc.store.set("google_refresh_token", self.google.refresh)
        self.svc.store.set("google_scopes", gp.SCOPE_READONLY)
        with self.assertRaises(gp.PickerError) as caught:
            self.picker.list()
        self.assertEqual(caught.exception.code, "need_consent")

    # ------------------------------------------------------------------ listing
    def query_of(self, last: int = -1) -> dict:
        request = [r for r in self.google.requests if r.url.path == "/drive/v3/files"][last]
        return dict(request.url.params)

    def test_listing_views_and_items(self) -> None:
        self.connect()
        first = self.picker.list(view="folder", folder="root")
        params = self.query_of()
        self.assertIn("'root' in parents", params["q"])
        self.assertIn("trashed = false", params["q"])
        self.assertEqual(params["orderBy"], "folder,name_natural")
        self.assertTrue(first["next"], "pages of 4")
        rows, token = list(first["files"]), first["next"]
        while token:
            page = self.picker.list(view="folder", folder="root", page_token=token)
            rows += page["files"]
            token = page["next"]
        self.assertEqual(self.query_of()["pageToken"], "8")
        names = {row["name"] for row in rows}
        self.assertIn("Plan Q4", names)
        self.assertNotIn("Budget", names, "files of sub-folders are not listed in root")
        items = {row["id"]: row for row in rows}
        self.assertEqual(items["fold1"]["kind"], "folder")
        self.assertFalse(items["fold1"]["attachable"])
        self.assertEqual(items["doc1"]["kind"], "doc")
        self.assertTrue(items["doc1"]["google"])
        self.assertEqual(items["doc1"]["formats"], ["docx", "pdf", "md", "txt"])
        self.assertTrue(items["big1"]["too_big"])
        self.assertFalse(items["form1"]["attachable"], "forms cannot be exported")
        self.assertFalse(items["lock1"]["attachable"], "the owner disabled downloads")
        self.assertEqual((items["short1"]["kind"], items["short1"]["target"], items["short1"]["shortcut"]), ("doc", "doc1", True))
        self.assertTrue(items["img1"]["thumb"])

        sub = self.picker.list(view="folder", folder="fold1")
        self.assertEqual({row["name"] for row in sub["files"]}, {"Budget", "Pitch"})
        self.assertEqual(self.picker.list(view="starred")["files"][0]["name"], "Plan Q4")
        shared = self.picker.list(view="shared")["files"]
        self.assertEqual([row["owner"] for row in shared], ["Friend"])
        self.assertFalse(shared[0]["mine"])
        self.picker.list(view="recent")
        self.assertEqual(self.query_of()["orderBy"], "viewedByMeTime desc")
        self.picker.list(view="folder", order="modified")
        self.assertEqual(self.query_of()["orderBy"], "folder,modifiedTime desc")

    def test_search_filters_and_escaping(self) -> None:
        self.connect()
        found = self.picker.list(view="search", query="plan")
        self.assertIn("Plan Q4", {row["name"] for row in found["files"]})
        self.assertNotIn("orderBy", self.query_of(), "Drive cannot sort fullText results")
        self.picker.list(view="search", query="it's a \\ test")
        self.assertIn("name contains 'it\\'s a \\\\ test'", self.query_of()["q"])
        self.assertEqual(self.picker.list(view="search", query="")["files"], [])
        images = self.picker.list(view="folder", kind="images")["files"]
        self.assertEqual([row["name"] for row in images], ["photo.png"])
        sheets = self.picker.list(view="folder", folder="fold1", kind="sheets")["files"]
        self.assertEqual([row["name"] for row in sheets], ["Budget"])
        self.assertIn("mimeType = 'application/vnd.google-apps.folder'", self.query_of()["q"], "folders stay visible to browse")
        # Injection attempts in ids / tokens are dropped, not sent.
        self.picker.list(view="folder", folder="x' or name contains 'a", page_token="bad token!")
        params = self.query_of()
        self.assertIn("'root' in parents", params["q"])
        self.assertNotIn("pageToken", params)

    def test_breadcrumbs(self) -> None:
        self.connect()
        self.assertEqual(self.picker.folder_path("fold1"), [{"id": "fold1", "name": "Projects"}])

    # ------------------------------------------------------------------ downloads
    def test_exports_and_downloads(self) -> None:
        self.connect()
        doc = self.picker.download("doc1")
        self.assertEqual((doc["name"], doc["mime"], doc["format"]), ("Plan Q4.docx", gp.DOCX, "docx"))
        export = [r for r in self.google.requests if r.url.path.endswith("/export")][-1]
        self.assertEqual(export.url.params["mimeType"], gp.DOCX)
        self.assertEqual(self.picker.download("doc1", "pdf")["name"], "Plan Q4.pdf")
        self.assertEqual(self.picker.download("doc1", "nonsense")["format"], "docx", "unknown format → default")
        sheet = self.picker.download("sheet1")
        self.assertEqual((sheet["name"], sheet["mime"], sheet["note"]), ("Budget.md", "text/markdown", "sheets_md"))
        text = sheet["data"].decode()
        self.assertIn("| Item | Cost |", text)
        self.assertIn("| Food \\| drinks | 40 |", text, "pipes inside cells are escaped")
        self.assertEqual(self.picker.download("sheet1", "xlsx")["name"], "Budget.xlsx")
        self.assertEqual(self.picker.download("slides1")["name"], "Pitch.pdf")
        short = self.picker.download("short1")
        self.assertEqual(short["name"], "Plan Q4.docx", "shortcuts resolve to their target")
        pdf = self.picker.download("pdf1")
        self.assertEqual((pdf["name"], pdf["data"]), ("report.pdf", b"%PDF-1.4 tiny"))
        sales = self.picker.download("xl1")
        self.assertEqual(sales["name"], "Sales.md")
        self.assertIn("| North | 10 |", sales["data"].decode())
        self.assertEqual(self.picker.download("xl1", "raw")["name"], "Sales.xlsx")

    def test_download_errors_and_limits(self) -> None:
        self.connect()
        cases = {"big1": "too_big", "nosz": "too_big", "fold1": "folder", "form1": "unsupported",
                 "lock1": "forbidden", "missing": "not_found", "../etc": "bad_id"}
        for fid, code in cases.items():
            with self.assertRaises(gp.PickerError, msg=fid) as caught:
                self.picker.download(fid)
            self.assertEqual(caught.exception.code, code, fid)
        # A file without a reported size is cut while streaming: nothing past the limit is read into memory.
        self.assertEqual(gp.MAX_FILE, 5 * 1024 * 1024)

    def test_refresh_on_401(self) -> None:
        self.connect()
        self.picker.list()
        self.google.expire_next = True
        self.assertTrue(self.picker.list()["files"], "a 401 refreshes the token once and retries")
        tokens = [r for r in self.google.requests if str(r.url).startswith(drive.TOKEN_URL)]
        self.assertGreaterEqual(len(tokens), 2)
        # Round 30: one Google account — no second «Integrations» token to fall back to.
        self.svc.auth.disconnect(revoke=False)
        with self.assertRaises(gp.PickerError) as caught:
            self.picker.list()
        self.assertEqual(caught.exception.code, "not_connected")

    def test_thumbnail_host_check(self) -> None:
        self.connect()
        mime, data = self.picker.thumbnail("img1")
        self.assertEqual((mime, data), ("image/jpeg", b"JPEGDATA"))
        self.google.files["pdf1"]["thumbnailLink"] = "http://169.254.169.254/latest"
        with self.assertRaises(gp.PickerError):
            self.picker.thumbnail("pdf1")
        self.google.files["doc1"]["thumbnailLink"] = "https://evil.example.com/x.googleusercontent.com"
        with self.assertRaises(gp.PickerError):
            self.picker.thumbnail("doc1")

    # ------------------------------------------------------------------ routes
    def test_routes(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.state.sync_service = self.svc
        gp.register(app, transport=self.transport)
        client = TestClient(app)
        self.assertEqual(client.get("/api/pg/gdrive/status").json()["state"], "not_connected")
        response = client.get("/api/pg/gdrive/list")
        self.assertEqual((response.status_code, response.json()["code"]), (403, "not_connected"))
        self.connect()
        data = client.get("/api/pg/gdrive/list", params={"view": "folder", "folder": "fold1", "crumbs": 1}).json()
        self.assertEqual(data["path"], [{"id": "fold1", "name": "Projects"}])
        response = client.post("/api/pg/gdrive/download", json={"id": "sheet1", "format": "md"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(urllib.parse.unquote(response.headers["x-memu-name"]), "Budget.md")
        self.assertTrue(response.headers["content-type"].startswith("text/markdown"))
        self.assertIn("| Rent | 100 |", response.text)
        response = client.post("/api/pg/gdrive/download", json={"id": "big1"})
        self.assertEqual((response.status_code, response.json()["code"]), (413, "too_big"))
        self.assertIn("error_en", response.json())
        self.assertEqual(client.get("/api/pg/gdrive/thumb", params={"id": "img1"}).content, b"JPEGDATA")
        self.assertTrue(client.post("/api/pg/gdrive/authorize").json()["ok"])
        self.assertTrue(client.post("/api/pg/gdrive/authorize/cancel").json()["ok"])
        for response in (client.get("/api/pg/gdrive/status"), client.get("/api/pg/gdrive/list")):
            self.assertNotIn(self.google.access, response.text)
            self.assertNotIn(self.google.refresh, response.text)


class HooksTest(unittest.TestCase):
    def test_wiring(self) -> None:
        api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        self.assertIn("gdrive_picker.register(app)", api)
        index = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        self.assertIn('/gdrive-picker.js?v=', index)
        self.assertIn('/gdrive-picker.css?v=', index)
        tools = (ROOT / "app" / "pgstatic" / "tools.js").read_text(encoding="utf-8")
        self.assertIn('id: "gdrive"', tools)
        self.assertIn("MemuDrivePick.open()", tools)
        for name in ("MemuAI.spec", "build.py"):
            self.assertIn("app.gdrive_picker", (ROOT / name).read_text(encoding="utf-8"), name)
        js = (ROOT / "app" / "pgstatic" / "gdrive-picker.js").read_text(encoding="utf-8")
        self.assertIn("addAttachmentFiles(", js, "files go through the same path as local attachments")
        for lang in ("ru", "en"):
            doc = (ROOT / "app" / "docs" / lang / "sync.md").read_text(encoding="utf-8")
            self.assertIn("drive.readonly", doc, lang)


if __name__ == "__main__":
    unittest.main()
