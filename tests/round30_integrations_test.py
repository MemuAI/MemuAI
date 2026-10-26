"""Round 30 (r30-integrations): one Google account (Gmail, Calendar, Drive, Sync, picker), Notion, build-time clients.

No network: Google/Notion are the in-memory fakes of tests/round30_mock_apis.py; the OAuth consent page is replaced by
a direct GET to MemuAI's loopback listener (what the browser does after «Allow»).
"""
from __future__ import annotations

import base64
import email
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

import httpx  # noqa: E402

from app import gdrive_sync, gdrive_sync_drive, google_account, integrations as ig, integrations_notion as notion, oauth_config  # noqa: E402
from tests.round30_mock_apis import FakeApis, install  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
G = "https://www.googleapis.com/auth/"

def visit(url: str) -> bytes:
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=10) as page:
        return page.read()

class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="r30int-"))
        self.fake = FakeApis()
        self.undo = install(self.fake)
        self.opened: list[str] = []
        self.old_service = gdrive_sync._SERVICE
        gdrive_sync._SERVICE = gdrive_sync.SyncService(self.tmp / "root", open_browser=self.opened.append)
        self.svc = gdrive_sync._SERVICE
        self.patches = [patch.object(ig, "MEMU_DIR", self.tmp / "memu"), patch.object(ig, "_sleep", lambda s: None),
                        patch.object(notion, "open_browser", self.opened.append)]
        for item in self.patches:
            item.start()
        google_account._migrated["done"] = False
        ig._tokens.clear()

    def tearDown(self) -> None:
        self.svc.auth.cancel_flow()
        notion.cancel_oauth()
        for item in self.patches:
            item.stop()
        self.undo()
        gdrive_sync._SERVICE = self.old_service
        google_account._migrated["done"] = False
        ig._tokens.clear()

    def own_client(self) -> None:
        google_account.save_client("123-abc.apps.googleusercontent.com", "GOCSPX-secret")

    def sign_in(self, services) -> dict:
        """connect() → the URL Google would show → the loopback redirect with a code."""
        self.own_client()
        result = google_account.connect(services)
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(result["auth_url"]).query))
        self.fake.granted = set(self.svc.auth.granted_scopes()) | set(params["scope"].split())
        page = visit(f"{params['redirect_uri']}?code=CODE&state={urllib.parse.quote(params['state'])}")
        self.assertIn("Google".encode(), page)
        return params

class GoogleAccountTest(Base):
    def test_sign_in_asks_only_for_chosen_services_and_adds_more_incrementally(self):
        with self.assertRaises(google_account.GoogleAccountError) as caught:
            google_account.connect(["gmail"])
        self.assertEqual(caught.exception.code, "no_client")
        params = self.sign_in(["gmail"])
        scopes = params["scope"].split()

        self.assertEqual(scopes, ["openid", G + "userinfo.email", "https://mail.google.com/", G + "gmail.settings.basic"])
        self.assertEqual((params["code_challenge_method"], params["include_granted_scopes"], params["access_type"]),
                         ("S256", "true", "offline"))
        self.assertTrue(params["redirect_uri"].startswith("http://127.0.0.1:"))
        self.assertEqual(self.opened[-1].split("?")[0], gdrive_sync_drive.AUTH_URL, "the system browser opens Google's page")
        status = google_account.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["account"]["email"], "me@example.com", "Gmail-only token: e-mail from OpenID userinfo")
        ready = {row["id"]: row["ready"] for row in status["services"]}
        self.assertEqual(ready, {"gmail": True, "calendar": False, "drive": False, "docs": False, "sheets": False,
                                 "tasks": False, "contacts": False})
        self.assertFalse(status["backups"])

        result = google_account.set_enabled("drive", True)
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(result["auth_url"]).query))
        self.assertEqual(params["scope"].split(), ["openid", G + "userinfo.email", G + "drive"])
        self.fake.granted |= set(params["scope"].split())
        visit(f"{params['redirect_uri']}?code=CODE&state={urllib.parse.quote(params['state'])}")
        ready = {row["id"]: row["ready"] for row in google_account.status()["services"]}
        self.assertEqual(ready, {"gmail": True, "calendar": False, "drive": True, "docs": False, "sheets": False,
                                 "tasks": False, "contacts": False})

        dumped = json.dumps(google_account.status())
        self.assertNotIn(self.fake.refresh, dumped)
        self.assertNotIn("GOCSPX", dumped)

    def test_one_account_for_sync_and_the_picker(self):
        self.sign_in(["gmail", "calendar"])

        self.assertFalse(self.svc.status()["connected"])
        self.assertFalse(self.svc.ready())
        url = self.svc.auth.start_flow()["auth_url"]
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.assertEqual(params["scope"].split(), [gdrive_sync_drive.SCOPE_APPDATA, gdrive_sync_drive.SCOPE_FILE])
        self.fake.granted |= set(params["scope"].split())
        visit(f"{params['redirect_uri']}?code=CODE&state={urllib.parse.quote(params['state'])}")
        self.assertTrue(self.svc.status()["connected"])
        self.assertTrue(google_account.ready("gmail"), "the backup consent kept Gmail")

        from app import gdrive_picker
        picker = gdrive_picker.Picker(lambda: self.svc)
        self.assertEqual(picker.status()["state"], "need_consent")
        result = google_account.set_enabled("drive", True)
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(result["auth_url"]).query))
        self.fake.granted |= set(params["scope"].split())
        visit(f"{params['redirect_uri']}?code=CODE&state={urllib.parse.quote(params['state'])}")
        self.assertEqual(picker.status()["state"], "ready")

    def test_access_errors_and_sign_out_revokes(self):
        with self.assertRaises(ig.IntegrationError) as caught:
            ig.run_tool("gmail_search", {})
        self.assertIn("Войти через Google", str(caught.exception))
        self.sign_in(["gmail"])
        with self.assertRaises(ig.IntegrationError) as caught:
            ig.run_tool("calendar_list_events", {})
        self.assertIn("выключен", str(caught.exception))
        google_account._save_chosen(["gmail", "calendar"])
        with self.assertRaises(ig.IntegrationError) as caught:
            ig.run_tool("calendar_list_events", {})
        self.assertIn("разрешение", str(caught.exception))
        self.assertEqual({row["service"]: row["last_error"] for row in ig.status()["services"]}["google_calendar"], "",
                         "«not signed in / switched off» is not a broken connection")
        google_account.disconnect()
        self.assertIn("google", self.fake.revoked)
        self.assertFalse(self.svc.auth.connected())
        self.assertEqual(google_account.chosen(), [])

    def test_legacy_hand_typed_keys_become_the_account(self):
        legacy = {"client_id": "old.apps.googleusercontent.com", "client_secret": "s", "refresh_token": "1//legacy",
                  "calendar_id": "team@group.calendar.google.com", "account": "me@example.com"}
        (self.tmp / "memu").mkdir(parents=True, exist_ok=True)
        (self.tmp / "memu" / "integrations.json").write_text(json.dumps({"google_calendar": legacy}), encoding="utf-8")
        status = google_account.status()
        self.assertTrue(status["connected"])
        self.assertEqual(self.svc.settings()["client_id"], "old.apps.googleusercontent.com")
        self.assertEqual({row["id"]: row["ready"] for row in status["services"]}["calendar"], True)
        store = ig._load_store()
        self.assertNotIn("google_calendar", store, "no second copy of the grant")
        self.assertEqual(google_account.default_calendar(), "team@group.calendar.google.com")

    def test_build_and_env_clients(self):
        with patch.dict(os.environ, {"MEMUAI_GOOGLE_CLIENT_ID": "env.apps.googleusercontent.com", "MEMUAI_GOOGLE_CLIENT_SECRET": "x"}):
            self.assertEqual(oauth_config.client("google"), ("env.apps.googleusercontent.com", "x", "env"))
            self.assertEqual(self.svc.auth.client()[2], "bundled")
            self.assertTrue(google_account.status()["client"]["bundled"])
            self.own_client()
            self.assertEqual(self.svc.auth.client()[2], "user", "the user's own client wins")
        fake_module = type("M", (), {"NOTION_CLIENT_ID": "nid", "NOTION_CLIENT_SECRET": "nsec"})
        with patch.dict("sys.modules", {"app.oauth_build": fake_module}):
            import app
            with patch.object(app, "oauth_build", fake_module, create=True):
                self.assertEqual(oauth_config.client("notion"), ("nid", "nsec", "build"))
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        self.assertIn("MEMUAI_GOOGLE_CLIENT_ID", build)
        self.assertIn("app.oauth_build", build)

class GoogleToolsTest(Base):
    def setUp(self) -> None:
        super().setUp()
        self.sign_in(["gmail", "calendar", "drive"])

    def test_gmail_search_read_draft_send(self):
        found = ig.run_tool("gmail_search", {"query": "from:anna отчёт", "limit": 5})
        self.assertEqual(found["messages"][0]["from"], "Анна Петрова <anna@example.com>")
        self.assertTrue(found["messages"][0]["unread"])
        letter = ig.run_tool("gmail_read", {"message_id": "m1"})
        self.assertIn("третий квартал", letter["text"])
        draft = ig.run_tool("gmail_create_draft", {"to": "anna@example.com", "subject": "Привет", "body": "Текст письма"})
        self.assertEqual(draft["draft_id"], "d1")
        raw = base64.urlsafe_b64decode(self.fake.drafts[0]["message"]["raw"] + "==")
        parsed = email.message_from_bytes(raw)
        self.assertEqual(parsed["To"], "anna@example.com")
        self.assertIn("Текст письма", parsed.get_payload(decode=True).decode("utf-8"))
        ig.run_tool("gmail_send", {"reply_to_message_id": "m1", "body": "Спасибо!"})
        sent = self.fake.sent[-1]
        self.assertEqual(sent["threadId"], "t1", "a reply stays in the thread")
        reply = email.message_from_bytes(base64.urlsafe_b64decode(sent["raw"] + "=="))
        self.assertEqual(reply["In-Reply-To"], "<m1@example.com>")
        self.assertTrue(str(email.header.make_header(email.header.decode_header(reply["Subject"]))).startswith("Re: "))
        self.assertIn("anna@example.com", reply["To"])
        ig.run_tool("gmail_send", {"draft_id": "d1"})
        self.assertEqual(self.fake.calls[-1][1].rsplit("/", 2)[-2:], ["drafts", "send"])
        with self.assertRaises(ig.IntegrationArgumentError):
            ig.run_tool("gmail_send", {"to": "not an address", "subject": "x", "body": "y"})

    def test_calendar_update_and_drive_upload(self):
        result = ig.run_tool("calendar_update_event", {"event_id": "e1", "start": "2026-10-12T11:00", "end": "2026-10-12T11:30",
                                                       "timezone": "Europe/Moscow"})
        self.assertTrue(result["updated"])
        method, url, body = self.fake.calls[-1]
        self.assertEqual(method, "PATCH")
        self.assertEqual(body, {"start": {"dateTime": "2026-10-12T11:00:00", "timeZone": "Europe/Moscow"},
                                "end": {"dateTime": "2026-10-12T11:30:00", "timeZone": "Europe/Moscow"}})
        with self.assertRaises(ig.IntegrationArgumentError):
            ig.run_tool("calendar_update_event", {"event_id": "e1", "start": "2026-10-12T11:00"})
        local = self.tmp / "report.pdf"
        local.write_bytes(b"%PDF-1.4 binary \x00\x01")
        uploaded = ig.run_tool("drive_upload_file", {"path": str(local)})
        self.assertEqual(uploaded["name"], "report.pdf")
        with self.assertRaises(ig.IntegrationArgumentError):
            ig.run_tool("drive_upload_file", {"path": "relative.txt"})
        ig.run_tool("drive_list_files", {"query": "отчёт", "search_content": True})
        q = [c for c in self.fake.calls if c[1].endswith("/drive/v3/files")]
        self.assertTrue(q)

    def test_status_lists_services_tools_and_confirmations(self):
        rows = {row["service"]: row for row in ig.status()["services"]}
        self.assertEqual(list(rows), ["github", "gmail", "google_calendar", "google_drive", "google_docs", "google_sheets",
                                      "google_tasks", "google_contacts", "notion", "telegram_use", "chrome_extension"])
        self.assertTrue(rows["gmail"]["connected"])
        self.assertEqual(rows["gmail"]["account"], "me@example.com")
        self.assertIn("gmail_send", rows["gmail"]["write_tools"])
        self.assertIn("gmail_search{query,limit,page_token,include_spam_trash}", rows["gmail"]["tools"])
        self.assertIn("gmail_send", ig.status()["always_confirm"])
        self.assertIn("gmail_delete", ig.status()["always_confirm"])
        test = ig.test_connection("gmail")
        self.assertTrue(test["ok"])

class NotionTest(Base):
    def test_token_connect_tools_and_disconnect(self):
        with self.assertRaises(ig.IntegrationArgumentError):
            notion.save_token("hello")
        status = notion.save_token("  Bearer " + self.fake.notion_token + "\n")
        self.assertTrue(status["connected"])
        self.assertEqual(status["account"], "Мой Notion")
        self.assertNotIn(self.fake.notion_token, (self.tmp / "memu" / "integrations.json").read_text(encoding="utf-8"),
                         "the token lives in the protected store, not in integrations.json")
        found = ig.run_tool("notion_search", {"query": "планы"})
        self.assertEqual(found["results"][0]["title"], "Планы на неделю")
        page = ig.run_tool("notion_read_page", {"page_id": "https://www.notion.so/Plany-11111111111111111111111111111111"})
        self.assertIn("## Понедельник", page["text"])
        self.assertIn("- [ ] Созвон с командой", page["text"])
        content = "# Итоги\n\n- пункт\n- [x] сделано\n\n```py\nprint(1)\n```\n" + "\n".join("строка %d" % i for i in range(3))
        created = ig.run_tool("notion_create_page", {"parent_id": "11111111111111111111111111111111", "title": "Итоги", "content": content})
        self.assertTrue(created["url"])
        children = self.fake.created_pages[-1]["children"]
        self.assertEqual([b["type"] for b in children], ["heading_1", "bulleted_list_item", "to_do", "code", "paragraph"])
        self.assertEqual(self.fake.created_pages[-1]["parent"], {"page_id": "11111111-1111-1111-1111-111111111111"})
        many = "\n".join("- пункт %d" % i for i in range(230))
        result = ig.run_tool("notion_append", {"page_id": "11111111111111111111111111111111", "content": many})
        self.assertEqual(result["appended_blocks"], 230)
        self.assertEqual([len(row["children"]) for row in self.fake.appended], [100, 100, 30])
        with self.assertRaises(ig.IntegrationArgumentError) as caught:
            ig.run_tool("notion_read_page", {"page_id": "99999999999999999999999999999999"})
        self.assertIn("Connections", str(caught.exception))
        notion.disconnect()
        self.assertFalse(notion.connected())
        with self.assertRaises(ig.IntegrationError):
            ig.run_tool("notion_search", {})

    def test_oauth_sign_in_with_a_public_integration(self):
        self.assertFalse(notion.status()["oauth"]["available"])
        notion.save_client("0f0e0d0c-0b0a-4908-8706-050403020100", "secret_public")
        self.assertTrue(notion.status()["oauth"]["available"])
        result = notion.start_oauth()
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(result["auth_url"]).query))
        self.assertEqual((params["owner"], params["redirect_uri"]), ("user", notion.REDIRECT_URI))
        self.assertEqual(self.opened[-1], result["auth_url"])
        page = visit(f"{notion.REDIRECT_URI}?code=abc&state={urllib.parse.quote(params['state'])}")
        self.assertIn("Notion".encode(), page)
        status = notion.status()
        self.assertEqual((status["connected"], status["mode"], status["account"]), (True, "oauth", "Мой Notion"))
        token_call = [c for c in self.fake.calls if c[1].endswith("/oauth/token")][-1]
        self.assertEqual(token_call[2]["grant_type"], "authorization_code")
        notion.disconnect()
        self.assertIn("notion", self.fake.revoked)

class WiringTest(unittest.TestCase):
    def test_window_and_server_agree_on_writes(self):

        js = (ROOT / "app/pgstatic/app.js").read_text(encoding="utf-8")
        block = js[js.index("const INTEGRATION_WRITE_TOOLS"):js.index("const TOOL_RESULT_CHARS")]
        self.assertIn("integrationsCache.write_tools", block)
        self.assertIn("integrationsCache.always_confirm", block)
        self.assertIn("MemuIntegrations.WRITE", block)
        ui_js = (ROOT / "app/pgstatic/integrations-ui.js").read_text(encoding="utf-8")
        if not shutil.which("node"):
            self.skipTest("Node is needed to inspect the actual runtime permission sets")
        script = "const fs=require('fs'),vm=require('vm');const c={window:{}};vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);console.log(JSON.stringify({writes:[...c.window.MemuIntegrations.WRITE],always:[...c.window.MemuIntegrations.ALWAYS]}));"
        result = subprocess.run(["node", "-e", script, str(ROOT / "app/pgstatic/integrations-ui.js")],
                                capture_output=True, text=True, check=True)
        sets = json.loads(result.stdout)
        writes, always = set(sets["writes"]), set(sets["always"])

        from app.browser_extension import WRITE as chrome_writes
        self.assertEqual(writes | chrome_writes, ig.WRITE_TOOLS)
        self.assertEqual(always, ig.ALWAYS_CONFIRM)
        self.assertIn("INTEGRATION_ALWAYS_CONFIRM.has(name)", js)
        self.assertIn("MemuIntegrations.confirm(name, args)", js)
        api = (ROOT / "app/playground_api.py").read_text(encoding="utf-8")
        self.assertIn("tool in integrations_api.ALWAYS_CONFIRM", api)
        self.assertIn("integrations_routes.register(app)", api)
        index = (ROOT / "app/pgstatic/index.html").read_text(encoding="utf-8")
        self.assertIn("/integrations-ui.js?v=", index)
        self.assertIn("/integrations-ui.css?v=", index)
        ui = (ROOT / "app/pgstatic/integrations-ui.js").read_text(encoding="utf-8")
        for name in ig.WRITE_TOOLS - chrome_writes:
            self.assertIn(name + ":", ui, "a readable confirmation card for " + name)

    def test_quick_ask_has_read_only_integration_tools(self):
        from app import quick_integrations
        names = set(quick_integrations.TOOLS)
        self.assertTrue(names)
        self.assertFalse(names & ig.WRITE_TOOLS, "writes need the main window's confirmation")
        manifest = json.loads((ROOT / "app/pgstatic/quick/plugins/index.json").read_text(encoding="utf-8"))
        self.assertIn("app.quick_integrations", [row.get("backend") for row in manifest["plugins"] if isinstance(row, dict)])

class RoutesTest(Base):
    def test_routes(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import integrations_api
        app = FastAPI()
        integrations_api.register(app)
        client = TestClient(app)
        self.assertFalse(client.get("/api/pg/integrations/google").json()["connected"])
        response = client.post("/api/pg/integrations/google", json={"action": "connect", "services": ["gmail"]})
        self.assertEqual((response.status_code, response.json()["code"]), (400, "no_client"))
        response = client.post("/api/pg/integrations/google", json={"action": "client", "client_id": "nope"})
        self.assertEqual(response.json()["code"], "bad_client")
        client.post("/api/pg/integrations/google", json={"action": "client", "client_id": "1-a.apps.googleusercontent.com", "client_secret": "GOCSPX-z"})
        data = client.post("/api/pg/integrations/google", json={"action": "connect", "services": ["calendar"]}).json()
        self.assertIn("accounts.google.com", data["auth_url"])
        self.assertEqual(data["status"]["flow"]["state"], "waiting")
        self.assertNotIn("GOCSPX", json.dumps(data))
        client.post("/api/pg/integrations/google", json={"action": "cancel"})
        data = client.post("/api/pg/integrations/notion", json={"action": "token", "token": self.fake.notion_token}).json()
        self.assertTrue(data["status"]["connected"])
        self.assertNotIn(self.fake.notion_token, json.dumps(client.get("/api/pg/integrations/notion").json()))

if __name__ == "__main__":
    unittest.main()
