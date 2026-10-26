"""Round 22: integrations audit — every flow of app/integrations.py against a mocked HTTP layer.

GitHub, Google Drive and Google Calendar: token storage and cleaning, OAuth refresh / expiry / revocation,
401/403/429/5xx in words, quiet retry of reads, timeouts, pagination, large and binary files, unicode names,
disconnect/reconnect, state shown in Settings, tool list exposed to the model, and the /api/pg/tools result.
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
import time
import unittest
from typing import Callable, List
from unittest.mock import patch

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

import httpx  # noqa: E402

from app import integrations as ig  # noqa: E402

class Mock:
    """Routes requests to a handler and records them."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler
        self.requests: List[httpx.Request] = []

    def client(self) -> httpx.Client:
        def wrapped(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self.handler(request)
        return httpx.Client(transport=httpx.MockTransport(wrapped), follow_redirects=True)

def j(status: int, data, headers=None) -> httpx.Response:
    return httpx.Response(status, json=data, headers=headers or {})

class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="memu-int-")
        self.patches = [patch.object(ig, "MEMU_DIR", __import__("pathlib").Path(self.tmp)),
                        patch.object(ig, "_sleep", lambda s: None)]
        for item in self.patches:
            item.start()
        ig._tokens.clear()

    def tearDown(self) -> None:
        for item in self.patches:
            item.stop()
        ig._tokens.clear()
        from app import gdrive_sync, google_account
        gdrive_sync._SERVICE = getattr(self, "_old_service", gdrive_sync._SERVICE)
        google_account._migrated["done"] = False

    def use(self, handler) -> Mock:
        mock = Mock(handler)
        patcher = patch.object(ig, "_client", mock.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def github(self, **extra) -> None:
        ig.save_credentials("github", {"token": "ghp_secret1234", **extra})

    def google(self, service="google_drive", **extra) -> None:
        """Round 30: the shared Google account (Sync's grant) signed in with every service; its OAuth requests go
        through the same mocked ``ig._client`` as the API calls."""
        from app import gdrive_sync, gdrive_sync_drive, google_account
        if not hasattr(self, "_old_service"):
            self._old_service = gdrive_sync._SERVICE
            factory = patch.object(gdrive_sync_drive, "_client_factory", lambda transport=None: ig._client())
            factory.start()
            self.addCleanup(factory.stop)
            gdrive_sync._SERVICE = gdrive_sync.SyncService(self.tmp + "/root")
            gdrive_sync._SERVICE.save_settings({"client_id": "cid.apps.googleusercontent.com", "client_secret": "sec"})
        svc = gdrive_sync._SERVICE
        svc.store.set("google_refresh_token", "1//refresh")
        svc.store.set("google_scopes", " ".join(sorted(scope for name in google_account.ORDER
                                                       for scope in google_account.SERVICES[name]["request"])))
        google_account._save_chosen(list(google_account.ORDER))
        google_account._migrated["done"] = True

class StorageTest(Base):
    def test_token_cleaning_and_mask(self):
        ig.save_credentials("github", {"token": '  "Bearer ghp_abc DEF123"\n', "default_repo": "https://github.com/Yono/memu/tree/main/app"})
        stored = ig._load_store()["github"]
        self.assertEqual(stored["token"], "ghp_abcDEF123")
        self.assertEqual(stored["default_repo"], "Yono/memu")
        public = ig.status()["services"][0]
        self.assertEqual(public["values"]["token"], "\u2022" * 8 + "F123")
        self.assertNotIn("ghp_abcDEF123", json.dumps(ig.status()))
        self.assertEqual(public["state"], "unverified")

        ig.save_credentials("github", {"token": "", "default_repo": "a/b"})
        ig.save_credentials("github", {"token": "\u2022" * 8 + "F123"})
        self.assertEqual(ig._load_store()["github"]["token"], "ghp_abcDEF123")

    def test_disconnect_and_reconnect_drop_cached_google_token(self):
        self.google()
        ig._tokens["google_drive"] = {"token": "old", "expires": time.time() + 3000}
        ig.disconnect("google_drive")
        self.assertNotIn("google_drive", ig._tokens)
        state = lambda: {row["service"]: row["state"] for row in ig.status()["services"]}
        self.assertEqual(state()["google_drive"], "off")
        self.assertEqual(state()["gmail"], "ok")
        with self.assertRaises(ig.IntegrationError):
            ig.run_tool("drive_list_files", {})
        self.google()
        self.assertEqual(state()["google_drive"], "ok")

class GitHubTest(Base):
    def test_status_success_marks_checked_and_clears_error(self):
        self.github()
        ig._remember("github", error="old failure")
        self.use(lambda r: j(200, {"login": "yono", "public_repos": 3}))
        self.assertTrue(ig.test_connection("github")["ok"])
        row = ig.status()["services"][0]
        self.assertEqual((row["state"], row["account"], row["last_error"]), ("ok", "yono", ""))

    def test_tool_success_clears_stale_error(self):
        self.github()
        ig._remember("github", error="GitHub 503")
        self.use(lambda r: j(200, []))
        ig.run_tool("github_list_issues", {"repo": "a/b"})
        self.assertEqual(ig.status()["services"][0]["last_error"], "")

    def test_error_messages(self):
        self.github()
        cases = [
            (j(401, {"message": "Bad credentials"}), "отклонил токен"),
            (j(403, {"message": "API rate limit exceeded"}, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(time.time()) + 600)}), "лимит запросов"),
            (j(429, {"message": "slow down"}, {"retry-after": "60"}), "лимит запросов"),
            (j(403, {"message": "Resource not accessible by personal access token"}), "не хватает прав"),
            (j(404, {"message": "Not Found"}), "не найден"),
            (j(422, {"message": "Validation Failed", "errors": [{"message": "title is too long"}]}), "title is too long"),
            (j(500, {}), "временно недоступен"),
        ]
        for response, text in cases:
            self.use(lambda r, response=response: response)
            with self.assertRaises(ig.IntegrationError) as ctx:
                ig.run_tool("github_repo_info", {"repo": "a/b"})
            self.assertIn(text, str(ctx.exception))
        self.assertEqual(ig.status()["services"][0]["state"], "error")

    def test_reads_retry_once_on_503_and_timeout_but_writes_do_not(self):
        self.github()
        calls = {"n": 0}

        def flaky(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return j(503, {})
            return j(200, {"full_name": "a/b"})
        self.use(flaky)
        self.assertEqual(ig.run_tool("github_repo_info", {"repo": "a/b"})["full_name"], "a/b")
        self.assertEqual(calls["n"], 2)

        def timeout(request):
            raise httpx.ReadTimeout("timed out", request=request)
        mock = self.use(timeout)
        with self.assertRaises(ig.IntegrationError) as ctx:
            ig.run_tool("github_repo_info", {"repo": "a/b"})
        self.assertIn("не ответил", str(ctx.exception))
        self.assertEqual(len(mock.requests), 2)
        mock = self.use(lambda r: j(503, {}))
        with self.assertRaises(ig.IntegrationError):
            ig.run_tool("github_create_issue", {"repo": "a/b", "title": "x"}, )
        self.assertEqual(len(mock.requests), 1)

    def test_repo_parsing(self):
        for value in ("a/b", "https://github.com/a/b", "https://github.com/a/b.git", "github.com/a/b/tree/main/src",
                      "git@github.com:a/b.git", "https://github.com/a/b/blob/main/README.md"):
            self.assertEqual(ig._parse_repo(value), "a/b", value)
        for bad in ("a", "a/b/c", "a/b?x=1", "../etc", "a/..", ""):
            with self.assertRaises(ig.IntegrationError, msg=bad):
                ig._parse_repo(bad)

    def test_bad_arguments_do_not_mark_connection_broken(self):
        self.github()
        self.use(lambda r: j(200, []))
        with self.assertRaises(ig.IntegrationArgumentError):
            ig.run_tool("github_read_file", {"repo": "a/b", "path": ""})
        self.assertEqual(ig.status()["services"][0]["last_error"], "")

        self.assertEqual(ig.run_tool("github_list_commits", {"repo": "a/b", "limit": "abc"}), {"commits": []})

    def test_list_repos_query_walks_pages(self):
        self.github()
        page1 = [{"full_name": "a/r%d" % i} for i in range(100)]
        page2 = [{"full_name": "a/memuai"}] + [{"full_name": "a/x%d" % i} for i in range(20)]

        def handler(request):
            page = request.url.params.get("page")
            return j(200, page1 if page == "1" else page2 if page == "2" else [])
        mock = self.use(handler)
        result = ig.run_tool("github_list_repos", {"query": "memuai", "limit": 5})
        self.assertEqual([r["full_name"] for r in result["repos"]], ["a/memuai"])
        self.assertEqual(len(mock.requests), 2)
        mock = self.use(lambda r: j(200, page1))
        self.assertEqual(ig.run_tool("github_list_repos", {"limit": 7})["count"], 7)
        self.assertEqual(mock.requests[0].url.params.get("per_page"), "7")

    def test_read_file_small_large_binary_and_unicode_path(self):
        self.github()
        text = "Привет, мир\n" * 3

        def handler(request):
            if "vnd.github.raw" in request.headers.get("accept", ""):
                return httpx.Response(200, content=("x" * (ig.MAX_DOWNLOAD + 5000)).encode())
            path = request.url.path
            if path.endswith("big.txt"):
                return j(200, {"type": "file", "path": "big.txt", "size": 5_000_000, "encoding": "none", "content": ""})
            if path.endswith("logo.png"):
                return j(200, {"type": "file", "path": "logo.png", "size": 12,
                               "content": base64.b64encode(b"\x89PNG\x00\x00binary").decode(), "encoding": "base64"})
            return j(200, {"type": "file", "path": "доки/файл.md", "size": 10,
                           "content": base64.b64encode(text.encode()).decode(), "encoding": "base64"})
        mock = self.use(handler)
        small = ig.run_tool("github_read_file", {"repo": "a/b", "path": "доки/файл.md"})
        self.assertEqual(small["text"], text)
        self.assertIn("/contents/%D0%B4%D0%BE%D0%BA%D0%B8/", str(mock.requests[-1].url))
        big = ig.run_tool("github_read_file", {"repo": "a/b", "path": "big.txt"})
        self.assertTrue(big["truncated"])
        self.assertEqual(len(big["text"]), ig.MAX_TEXT)
        binary = ig.run_tool("github_read_file", {"repo": "a/b", "path": "logo.png"})
        self.assertTrue(binary["binary"])
        self.assertEqual(binary["text"], "")

    def test_write_file_creates_updates_and_surfaces_auth_errors(self):
        self.github()
        seen = []

        def handler(request):
            seen.append((request.method, request.url.path, request.content))
            if request.method == "GET":
                return j(404, {"message": "Not Found"})
            return j(201, {"commit": {"sha": "abcdef1234567"}, "content": {"html_url": "u"}})
        self.use(handler)
        result = ig.run_tool("github_write_file", {"repo": "a/b", "path": "новый.md", "content": "ё", "message": "m"})
        self.assertEqual(result["commit"], "abcdef1234")
        body = json.loads(seen[-1][2])
        self.assertNotIn("sha", body)
        self.assertEqual(base64.b64decode(body["content"]).decode(), "ё")
        self.use(lambda r: j(200, {"sha": "old"}) if r.method == "GET" else j(200, {"commit": {"sha": "1"}}))
        ig.run_tool("github_write_file", {"repo": "a/b", "path": "x", "content": "y"})

        mock = self.use(lambda r: j(401, {"message": "Bad credentials"}))
        with self.assertRaises(ig.IntegrationError):
            ig.run_tool("github_write_file", {"repo": "a/b", "path": "x", "content": "y"})
        self.assertEqual([r.method for r in mock.requests], ["GET"])
        self.use(lambda r: j(200, [{"name": "a"}]))
        with self.assertRaises(ig.IntegrationArgumentError):
            ig.run_tool("github_write_file", {"repo": "a/b", "path": "dir", "content": "y"})

class GoogleTest(Base):
    def token_ok(self, request, counter=None):
        if counter is not None:
            counter["token"] = counter.get("token", 0) + 1
        return j(200, {"access_token": "at-%d" % (counter or {}).get("token", 1), "expires_in": 3599})

    def test_refresh_cached_and_expiry(self):
        self.google()
        count = {}

        def handler(request):
            if "oauth2" in request.url.host:
                return self.token_ok(request, count)
            return j(200, {"files": []})
        self.use(handler)
        ig.run_tool("drive_list_files", {})
        ig.run_tool("drive_list_files", {})
        self.assertEqual(count["token"], 1)
        ig._tokens["google_drive"]["expires"] = time.time() + 30
        from app import gdrive_sync
        gdrive_sync._SERVICE.auth._access = ("", 0.0)
        ig.run_tool("drive_list_files", {})
        self.assertEqual(count["token"], 2)

    def test_revoked_access_token_is_refreshed_once(self):
        self.google()
        count = {}
        api_calls = []

        def handler(request):
            if "oauth2" in request.url.host:
                return self.token_ok(request, count)
            api_calls.append(request.headers["authorization"])
            return j(401, {"error": {"code": 401, "message": "Invalid Credentials"}}) if len(api_calls) == 1 else j(200, {"files": [{"id": "1"}]})
        self.use(handler)
        self.assertEqual(ig.run_tool("drive_list_files", {})["files"][0]["id"], "1")
        self.assertNotEqual(api_calls[0], api_calls[1])

    def test_oauth_errors_in_words(self):
        self.google()

        for payload, text in (({"error": "invalid_client", "error_description": "The OAuth client was not found."}, "Client ID"),
                              ({"error": "invalid_grant", "error_description": "Token has been expired or revoked."}, "войдите заново")):
            self.use(lambda r, payload=payload: j(400, payload))
            ig._tokens.clear()
            with self.assertRaises(ig.IntegrationError) as ctx:
                ig.run_tool("drive_list_files", {})
            self.assertIn(text, str(ctx.exception))
        self.use(lambda r: httpx.Response(200, text="<html>proxy login</html>"))
        with self.assertRaises(ig.IntegrationError):
            ig.run_tool("drive_list_files", {})

    def test_google_api_errors(self):
        self.google()
        cases = [
            (j(403, {"error": {"code": 403, "message": "Rate", "errors": [{"reason": "userRateLimitExceeded"}]}}), "лимит запросов"),
            (j(403, {"error": {"code": 403, "message": "Drive API has not been used", "status": "PERMISSION_DENIED",
                               "details": [{"reason": "SERVICE_DISABLED"}]}}), "выключен"),
            (j(403, {"error": {"code": 403, "message": "Request had insufficient authentication scopes.",
                               "details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}}), "scope"),
            (j(404, {"error": {"code": 404, "message": "File not found"}}), "не найдено"),
            (j(500, {"error": {"code": 500}}), "временно недоступен"),
        ]
        for response, text in cases:
            def handler(request, response=response):
                return self.token_ok(request) if "oauth2" in request.url.host else response
            self.use(handler)
            with self.assertRaises(ig.IntegrationError) as ctx:
                ig.run_tool("drive_read_file", {"file_id": "x"})
            self.assertIn(text, str(ctx.exception))

    def test_drive_query_escaping_and_unicode(self):
        self.google()
        mock = self.use(lambda r: self.token_ok(r) if "oauth2" in r.url.host else j(200, {"files": [], "nextPageToken": "n"}))
        result = ig.run_tool("drive_list_files", {"query": "O'Brien отчёт", "folder_id": "abc'd"})
        self.assertTrue(result["more"])
        q = mock.requests[-1].url.params["q"]
        self.assertIn("name contains 'O\\'Brien отчёт'", q)
        self.assertIn("'abc\\'d' in parents", q)

    def test_drive_read_text_binary_large_and_docs(self):
        self.google()

        def handler(request):
            if "oauth2" in request.url.host:
                return self.token_ok(request)
            path, params = request.url.path, request.url.params
            if params.get("fields"):
                kind = path.rsplit("/", 1)[-1]
                mime = {"doc": "application/vnd.google-apps.document", "pdf": "application/pdf",
                        "big": "text/plain", "txt": "text/plain", "form": "application/vnd.google-apps.form"}[kind]
                return j(200, {"id": kind, "name": "Файл " + kind, "mimeType": mime, "size": "123"})
            if path.endswith("/export"):
                return httpx.Response(200, content="Документ".encode())
            if path.endswith("/big"):
                return httpx.Response(200, content=b"y" * (ig.MAX_DOWNLOAD * 3))
            if path.endswith("/pdf"):
                raise AssertionError("a PDF must not be downloaded")
            return httpx.Response(200, content="\ufeffтекст".encode("utf-8"))
        self.use(handler)
        self.assertEqual(ig.run_tool("drive_read_file", {"file_id": "doc"})["text"], "Документ")
        self.assertTrue(ig.run_tool("drive_read_file", {"file_id": "pdf"})["binary"])
        big = ig.run_tool("drive_read_file", {"file_id": "big"})
        self.assertTrue(big["truncated"])
        self.assertEqual(ig.run_tool("drive_read_file", {"file_id": "txt"})["text"], "текст")
        with self.assertRaises(ig.IntegrationArgumentError):
            ig.run_tool("drive_read_file", {"file_id": "form"})

    def test_drive_create_is_one_multipart_request(self):
        self.google()
        mock = self.use(lambda r: self.token_ok(r) if "oauth2" in r.url.host else j(200, {"id": "new", "name": "Заметка", "webViewLink": "l"}))
        result = ig.run_tool("drive_create_file", {"name": "Заметка", "content": "привет",
                                                   "mime": "application/vnd.google-apps.document", "folder_id": "f1"})
        self.assertEqual(result["id"], "new")
        api = [r for r in mock.requests if "oauth2" not in r.url.host]
        self.assertEqual(len(api), 1)
        self.assertEqual(api[0].url.params["uploadType"], "multipart")
        body = api[0].content.decode("utf-8")
        self.assertIn('"name": "Заметка"', body)
        self.assertIn('"parents": ["f1"]', body)
        self.assertIn("Content-Type: text/plain", body)
        self.assertIn("привет", body)

    def test_calendar_events_and_dates(self):
        self.google("google_calendar")
        sent = []

        def handler(request):
            if "oauth2" in request.url.host:
                return self.token_ok(request)
            sent.append(request)
            if request.method == "DELETE":
                return j(410, {"error": {"code": 410, "message": "Resource has been deleted"}})
            if request.method == "POST":
                return j(200, {"id": "e1", "htmlLink": "l", "status": "confirmed"})
            return j(200, {"items": [{"id": "p", "primary": True}] * 7})
        self.use(handler)
        ig.run_tool("calendar_create_event", {"summary": "День рождения", "start": "2026-10-08", "end": "2026-10-08"})
        body = json.loads(sent[-1].content)
        self.assertEqual(body["end"]["date"], "2026-10-09")
        ig.run_tool("calendar_create_event", {"summary": "Встреча", "start": "2026-10-08T15:00", "end": "2026-10-08T16:00",
                                              "timezone": "Europe/Moscow", "location": "Офис"})
        body = json.loads(sent[-1].content)
        self.assertEqual(body["start"], {"dateTime": "2026-10-08T15:00:00", "timeZone": "Europe/Moscow"})
        self.assertEqual(body["location"], "Офис")
        with self.assertRaises(ig.IntegrationArgumentError):
            ig.run_tool("calendar_create_event", {"summary": "x", "start": "2026-10-08T16:00+03:00", "end": "2026-10-08T15:00+03:00"})
        with self.assertRaises(ig.IntegrationArgumentError):
            ig.run_tool("calendar_create_event", {"summary": "x", "start": "завтра", "end": "послезавтра"})
        result = ig.run_tool("calendar_delete_event", {"event_id": "e#1"})
        self.assertTrue(result["already_deleted"])
        self.assertIn("/events/e%231", str(sent[-1].url))
        check = ig.test_connection("google_calendar")
        self.assertEqual(check["detail"], "Календарей: 7")
        self.assertEqual(sent[-1].url.params["maxResults"], "250")

class ToolSchemaAndRouteTest(Base):
    def test_tool_list_matches_arguments_the_code_reads(self):
        self.assertIn("calendar_create_event{summary,start,end,description,location,calendar_id,timezone,attendees,recurrence,reminders,meet,send_updates}", ig.TOOLS["google_calendar"])
        self.assertIn("drive_update_file{file_id,content,mime}", ig.TOOLS["google_drive"])
        names = {tool.split("{")[0] for tools in ig.TOOLS.values() for tool in tools}
        self.assertTrue(ig.WRITE_TOOLS <= names)
        for name in names:
            self.assertTrue(ig.service_for_tool(name))

    def test_pg_tools_returns_integration_errors_as_results(self):
        from fastapi.testclient import TestClient
        from app.playground_api import create_app
        client = TestClient(create_app(), base_url="http://127.0.0.1")
        with patch.object(ig, "run_tool", side_effect=ig.IntegrationError("GitHub отклонил токен")):
            response = client.post("/api/pg/tools", json={"tool": "github_status", "args": {}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"tool": "github_status", "error": "GitHub отклонил токен"})
        response = client.post("/api/pg/tools", json={"tool": "github_write_file", "args": {}})
        self.assertIn("не подтвердил", response.json()["error"])

if __name__ == "__main__":
    unittest.main()
