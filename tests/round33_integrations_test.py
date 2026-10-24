"""Round 33 (r33-integrations): the full GitHub / Gmail / Drive / Calendar / Docs / Sheets / Tasks / Contacts / Notion
tool sets, scope growth with re-consent, «API is disabled» messages with a direct link, «Check APIs».

No network: GitHub and Google are the in-memory fakes of tests/round33_mock_apis.py; every test asserts the request
MemuAI sent (method, path, JSON body, query) against the shapes of the public API docs.
"""
from __future__ import annotations

import base64
import email
import email.policy
import json
import os
import re
import sys
import tempfile
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from app import gdrive_sync, google_account, integrations as ig, integrations_notion as notion  # noqa: E402
from app import integrations_github, integrations_gmail, integrations_notion_tools  # noqa: E402
from round33_mock_apis import Fake33, install  # noqa: E402

G = "https://www.googleapis.com/auth/"
GH = "https://api.github.com"
GM = "https://gmail.googleapis.com/gmail/v1/users/me"
DRV = "https://www.googleapis.com/drive/v3"
CAL = "https://www.googleapis.com/calendar/v3"
FULL = {"openid", G + "userinfo.email", "https://mail.google.com/", G + "gmail.settings.basic", G + "calendar", G + "drive",
        G + "documents", G + "spreadsheets", G + "tasks", G + "contacts"}


def visit(url: str) -> bytes:
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=10) as page:
        return page.read()


class Base(unittest.TestCase):
    services = ("gmail", "calendar", "drive", "docs", "sheets", "tasks", "contacts")

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="r33int-"))
        self.fake = Fake33()
        self.undo = install(self.fake)
        self.opened: list = []
        self.old_service = gdrive_sync._SERVICE
        gdrive_sync._SERVICE = gdrive_sync.SyncService(self.tmp / "root", open_browser=self.opened.append)
        self.svc = gdrive_sync._SERVICE
        self.patches = [patch.object(ig, "MEMU_DIR", self.tmp / "memu"), patch.object(ig, "_sleep", lambda s: None),
                        patch.object(notion, "open_browser", self.opened.append)]
        for item in self.patches:
            item.start()
        google_account._migrated["done"] = False
        ig._tokens.clear()
        ig.save_credentials("github", {"token": self.fake.github_token, "default_repo": "me/notes"})

    def tearDown(self) -> None:
        self.svc.auth.cancel_flow()
        for item in self.patches:
            item.stop()
        self.undo()
        gdrive_sync._SERVICE = self.old_service
        google_account._migrated["done"] = False
        ig._tokens.clear()

    def sign_in(self, services=None, granted=None) -> dict:
        google_account.save_client("123456789012-abc.apps.googleusercontent.com", "GOCSPX-secret")
        result = google_account.connect(list(services or self.services))
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(result["auth_url"]).query))
        self.fake.granted = set(granted) if granted is not None else (set(self.svc.auth.granted_scopes()) | set(params["scope"].split()))
        visit(f"{params['redirect_uri']}?code=CODE&state={urllib.parse.quote(params['state'])}")
        return params

    def last(self, method: str, suffix: str):
        for m, url, body in reversed(self.fake.calls):
            if m == method and url.endswith(suffix):
                return body
        self.fail("no %s …%s in %s" % (method, suffix, [(m, u[-60:]) for m, u, _b in self.fake.calls[-12:]]))

    def query(self, suffix: str) -> dict:
        for (m, url, _b), q in zip(reversed(self.fake.calls), reversed(self.fake.queries)):
            if url.endswith(suffix):
                return q
        return {}


# ============================================================================ GitHub
class GitHubTest(Base):
    def test_token_info_says_kind_and_missing_scopes(self):
        self.fake.github_scopes = "repo"
        info = ig.run_tool("github_status", {})
        self.assertEqual((info["login"], info["kind"]), ("me", "classic"))
        self.assertEqual(info["missing"], ["workflow", "gist", "delete_repo"])
        test = ig.test_connection("github")
        self.assertTrue(test["ok"])
        self.assertIn("не хватает: workflow, gist, delete_repo", test["detail"])

    def test_repositories_create_rename_visibility_topics_fork_delete(self):
        row = ig.run_tool("github_create_repo", {"name": "new-app", "description": "x", "private": "false", "license": "MIT"})
        self.assertEqual(row["full_name"], "me/new-app")
        self.assertEqual(self.last("POST", "/user/repos"),
                         {"name": "new-app", "description": "x", "private": False, "auto_init": True, "license_template": "mit"})
        ig.run_tool("github_create_repo", {"name": "team-app", "org": "acme"})
        self.assertTrue(any(u.endswith("/orgs/acme/repos") for _m, u, _b in self.fake.calls))
        row = ig.run_tool("github_update_repo", {"repo": "me/new-app", "new_name": "renamed", "private": True, "topics": "AI, tools"})
        self.assertEqual(self.last("PATCH", "/repos/me/new-app"), {"name": "renamed", "private": True})
        self.assertEqual(self.last("PUT", "/repos/me/renamed/topics"), {"names": ["ai", "tools"]})
        self.assertEqual(row["full_name"], "me/renamed")
        ig.run_tool("github_fork_repo", {"repo": "me/notes", "name": "notes-copy"})
        self.assertEqual(self.last("POST", "/repos/me/notes/forks"), {"name": "notes-copy"})
        # Deleting never falls back to the default repository.
        with self.assertRaises(ig.IntegrationArgumentError):
            ig.run_tool("github_delete_repo", {})
        self.assertEqual(ig.run_tool("github_delete_repo", {"repo": "https://github.com/me/renamed"}), {"deleted": True, "repo": "me/renamed"})
        self.assertNotIn("me/renamed", self.fake.repos)
        self.assertIn("github_delete_repo", ig.ALWAYS_CONFIRM)

    def test_files_delete_move_folder_and_multi_file_commit(self):
        result = ig.run_tool("github_delete_file", {"path": "README.md", "message": "bye"})
        self.assertEqual(self.last("DELETE", "/contents/README.md"), {"message": "bye", "sha": "sha-README.md"})
        self.assertTrue(result["deleted"])
        self.assertNotIn("README.md", self.fake.repos["me/notes"]["files"])
        moved = ig.run_tool("github_move_file", {"path": "docs", "new_path": "guide"})
        tree = self.last("POST", "/git/trees")
        self.assertEqual(tree["base_tree"], "tree-c0")
        self.assertIn({"path": "guide/a.md", "mode": "100644", "type": "blob", "sha": "blob-docs/a.md"}, tree["tree"])
        self.assertIn({"path": "docs/a.md", "mode": "100644", "type": "blob", "sha": None}, tree["tree"])
        self.assertEqual(self.last("POST", "/git/commits")["parents"], ["c0"])
        self.assertEqual(self.last("PATCH", "/git/refs/heads/main")["force"], False)
        self.assertEqual((moved["files"], sorted(self.fake.repos["me/notes"]["files"])), (2, ["guide/a.md", "guide/b.md"]))
        ig.run_tool("github_commit_files", {"files": [{"path": "src/app.py", "content": "print(1)"},
                                                      {"path": "img.png", "content_base64": "iVBORw0KGgo="}],
                                            "delete": ["guide/b.md"], "message": "feat"})
        tree = self.last("POST", "/git/trees")["tree"]
        self.assertIn({"path": "src/app.py", "mode": "100644", "type": "blob", "content": "print(1)"}, tree)
        self.assertIn({"path": "img.png", "mode": "100644", "type": "blob", "sha": "rawblob"}, tree)
        self.assertEqual(self.last("POST", "/git/blobs"), {"content": "iVBORw0KGgo=", "encoding": "base64"})
        deleted_folder = ig.run_tool("github_delete_file", {"path": "guide"})
        self.assertEqual(deleted_folder["files"], 1)
        with self.assertRaises(ig.IntegrationArgumentError):
            ig.run_tool("github_move_file", {"path": "../etc", "new_path": "x"})

    def test_branches_pulls_issues_labels(self):
        ig.run_tool("github_create_branch", {"branch": "feature/x"})
        self.assertEqual(self.last("POST", "/git/refs"), {"ref": "refs/heads/feature/x", "sha": "c0" * 20})
        with self.assertRaises(ig.IntegrationArgumentError):
            ig.run_tool("github_delete_branch", {"branch": "main"})
        ig.run_tool("github_delete_branch", {"branch": "feature/x"})
        self.assertNotIn("feature/x", self.fake.repos["me/notes"]["branches"])
        pr = ig.run_tool("github_create_pull", {"title": "Fix", "head": "feature/x", "body": "b", "draft": "true"})
        self.assertEqual(self.last("POST", "/pulls"), {"title": "Fix", "head": "feature/x", "base": "main", "body": "b", "draft": True})
        merged = ig.run_tool("github_merge_pull", {"number": pr["number"], "method": "squash"})
        self.assertEqual(self.last("PUT", "/merge"), {"merge_method": "squash"})
        self.assertTrue(merged["merged"])
        ig.run_tool("github_review_pull", {"number": "#101", "event": "approved"})
        self.assertEqual(self.last("POST", "/reviews"), {"event": "APPROVE", "body": ""})
        with self.assertRaises(ig.IntegrationArgumentError):
            ig.run_tool("github_review_pull", {"number": 101, "event": "REQUEST_CHANGES"})
        issue = ig.run_tool("github_create_issue", {"title": "Bug", "labels": "bug, ui", "assignees": ["me"]})
        self.assertEqual(self.last("POST", "/issues"), {"title": "Bug", "body": "", "labels": ["bug", "ui"], "assignees": ["me"]})
        ig.run_tool("github_update_issue", {"number": issue["number"], "state": "closed", "state_reason": "not_planned"})
        self.assertEqual(self.last("PATCH", "/issues/1"), {"state": "closed", "state_reason": "not_planned"})
        ig.run_tool("github_add_labels", {"number": 1, "labels": ["help wanted"]})
        ig.run_tool("github_remove_label", {"number": 1, "label": "help wanted"})
        self.assertTrue(any(u.endswith("/issues/1/labels/help%20wanted") for m, u, _b in self.fake.calls if m == "DELETE"))
        ig.run_tool("github_comment", {"number": 1, "body": "Thanks"})
        self.assertEqual(self.last("POST", "/issues/1/comments"), {"body": "Thanks"})

    def test_releases_gists_actions_collaborators(self):
        ig.run_tool("github_create_release", {"tag": "v2.0"})
        self.assertEqual(self.last("POST", "/releases"), {"tag_name": "v2.0", "name": "v2.0", "body": "", "draft": False,
                                                          "prerelease": False, "generate_release_notes": True})
        ig.run_tool("github_delete_release", {"tag": "v2.0", "delete_tag": True})
        self.assertTrue(any(m == "DELETE" and u.endswith("/git/refs/tags/v2.0") for m, u, _b in self.fake.calls))
        gist = ig.run_tool("github_create_gist", {"files": {"a.txt": "hi"}, "description": "d"})
        self.assertEqual(self.last("POST", "/gists"), {"description": "d", "public": False, "files": {"a.txt": {"content": "hi"}}})
        ig.run_tool("github_update_gist", {"gist_id": gist["id"], "files": [{"name": "a.txt", "delete": True}, {"name": "b.txt", "content": "x"}]})
        self.assertEqual(self.last("PATCH", "/gists/" + gist["id"]), {"files": {"a.txt": None, "b.txt": {"content": "x"}}})
        ig.run_tool("github_delete_gist", {"gist_id": gist["id"]})
        runs = ig.run_tool("github_list_runs", {"status": "failure"})
        self.assertEqual(runs["runs"][0]["id"], 555)
        run = ig.run_tool("github_get_run", {"run_id": 555})
        self.assertEqual(run["jobs"][0]["failed_steps"], ["pytest"])
        logs = ig.run_tool("github_job_logs", {"job_id": 777})
        self.assertIn("AssertionError", logs["log"])
        ig.run_tool("github_rerun", {"run_id": 555, "failed_only": True})
        self.assertTrue(any(u.endswith("/actions/runs/555/rerun-failed-jobs") for _m, u, _b in self.fake.calls))
        ig.run_tool("github_dispatch_workflow", {"workflow": "ci.yml", "inputs": {"level": 2}})
        self.assertEqual(self.last("POST", "/actions/workflows/ci.yml/dispatches"), {"ref": "main", "inputs": {"level": "2"}})
        ig.run_tool("github_add_collaborator", {"repo": "me/notes", "username": "@anna", "permission": "maintain"})
        self.assertEqual(self.last("PUT", "/collaborators/anna"), {"permission": "maintain"})
        ig.run_tool("github_remove_collaborator", {"repo": "me/notes", "username": "anna"})

    def test_forbidden_names_the_missing_permission(self):
        self.fake.github_403 = {"/repos/me/notes/forks": "fine"}
        with self.assertRaises(ig.IntegrationError) as caught:
            ig.run_tool("github_fork_repo", {"repo": "me/notes"})
        self.assertIn("administration: write", str(caught.exception))
        self.fake.github_403 = {"/repos/me/notes": "classic"}
        with self.assertRaises(ig.IntegrationError) as caught:
            ig.run_tool("github_delete_repo", {"repo": "me/notes"})
        self.assertIn("delete_repo", str(caught.exception))

    def test_every_listed_tool_is_dispatched(self):
        # Each tool line must reach real code: with empty arguments it either works or asks for an argument —
        # never «unknown tool».
        for line in ig.TOOLS["github"]:
            name = line.split("{")[0]
            try:
                ig.run_tool(name, {})
            except ig.IntegrationError as exc:
                self.assertNotIn("Неизвестный инструмент", str(exc), name)
        self.assertTrue(integrations_github.WRITE <= ig.WRITE_TOOLS)
        self.assertTrue(integrations_github.ALWAYS <= integrations_github.WRITE)


# ============================================================================ Google
class GmailTest(Base):
    def setUp(self) -> None:
        super().setUp()
        self.sign_in()

    def test_labels_archive_read_star_trash_delete(self):
        ig.run_tool("gmail_modify", {"message_ids": ["m1", "m2"], "add_labels": "Работа, Новая", "remove_labels": ["inbox"]})
        body = self.last("POST", "/messages/batchModify")
        self.assertEqual(body["ids"], ["m1", "m2"])
        self.assertEqual(body["removeLabelIds"], ["INBOX"])
        self.assertEqual(body["addLabelIds"][0], "Label_1")
        self.assertEqual(self.last("POST", "/labels")["name"], "Новая", "a missing label is created on the way")
        ig.run_tool("gmail_archive", {"message_ids": "m1"})
        self.assertEqual(self.last("POST", "/messages/batchModify"), {"ids": ["m1"], "addLabelIds": [], "removeLabelIds": ["INBOX"]})
        ig.run_tool("gmail_mark_read", {"message_ids": ["m2"], "read": False})
        self.assertEqual(self.last("POST", "/messages/batchModify")["addLabelIds"], ["UNREAD"])
        ig.run_tool("gmail_star", {"thread_id": "t1"})
        self.assertEqual(self.last("POST", "/threads/t1/modify"), {"addLabelIds": ["STARRED"], "removeLabelIds": []})
        ig.run_tool("gmail_trash", {"message_ids": ["m2"]})
        self.assertIn("TRASH", self.fake.messages["m2"]["labelIds"])
        ig.run_tool("gmail_untrash", {"message_ids": ["m2"]})
        self.assertNotIn("TRASH", self.fake.messages["m2"]["labelIds"])
        result = ig.run_tool("gmail_delete", {"message_ids": ["m2"]})
        self.assertEqual(self.last("POST", "/messages/batchDelete"), {"ids": ["m2"]})
        self.assertTrue(result["deleted_forever"])
        self.assertEqual(self.fake.deleted_mail, ["m2"])
        self.assertTrue({"gmail_delete", "gmail_send", "gmail_reply", "gmail_forward"} <= ig.ALWAYS_CONFIRM)
        with self.assertRaises(ig.IntegrationArgumentError):
            ig.run_tool("gmail_delete_label", {"label": "INBOX"})

    def test_bulk_by_query(self):
        result = ig.run_tool("gmail_bulk", {"query": "from:boris", "action": "trash", "limit": 50})
        self.assertEqual(self.query("/messages")["q"], "from:boris")
        self.assertEqual(self.last("POST", "/messages/batchModify")["addLabelIds"], ["TRASH"])
        self.assertGreaterEqual(result["matched"], 1)
        with self.assertRaises(ig.IntegrationArgumentError):
            ig.run_tool("gmail_bulk", {"query": "x", "action": "explode"})

    def test_reply_all_forward_with_attachments_and_upload(self):
        me_profile = ig.run_tool("gmail_reply", {"message_id": "m1", "body": "Ок", "reply_all": True, "draft": True})
        self.assertIn("draft_id", me_profile)
        raw = base64.urlsafe_b64decode(self.fake.drafts[-1]["message"]["raw"] + "==")
        letter = email.message_from_bytes(raw, policy=email.policy.default)
        self.assertEqual(letter["To"], "Анна Петрова <anna@example.com>")
        self.assertNotIn("me@example.com", str(letter["Cc"] or ""), "my own address is not copied into Cc")
        self.assertEqual(self.fake.drafts[-1]["message"]["threadId"], "t1")
        local = self.tmp / "отчёт.pdf"
        local.write_bytes(b"%PDF-1.4 data")
        ig.run_tool("gmail_send", {"to": "boris@example.com", "subject": "Файл", "body": "Во вложении", "attachments": [str(local)]})
        sent = email.message_from_bytes(base64.urlsafe_b64decode(self.fake.sent[-1]["raw"] + "=="), policy=email.policy.default)
        names = [part.get_filename() for part in sent.iter_attachments()]
        self.assertEqual(names, ["отчёт.pdf"])
        # Forward: the original's text is quoted and its attachments go along.
        self.fake.messages["m1"]["body"] = "Смотри файл"
        ig.run_tool("gmail_forward", {"message_id": "m1", "to": "boss@example.com", "body": "FYI"})
        fwd = email.message_from_bytes(base64.urlsafe_b64decode(self.fake.sent[-1]["raw"] + "=="), policy=email.policy.default)
        self.assertTrue(str(fwd["Subject"]).startswith("Fwd: "))
        self.assertIn("Forwarded message", fwd.get_body(("plain",)).get_content())
        # A big letter goes through the upload endpoint (multipart/related with message/rfc822).
        big = self.tmp / "big.bin"
        big.write_bytes(os.urandom(integrations_gmail.UPLOAD_OVER + 1000))
        ig.run_tool("gmail_send", {"to": "boris@example.com", "subject": "Большой", "body": "x", "attachments": str(big)})
        self.assertEqual(self.fake.sent[-1].get("upload"), True)
        self.assertEqual(self.query("/upload/gmail/v1/users/me/messages/send")["uploadType"], "multipart")
        with self.assertRaises(ig.IntegrationArgumentError):
            ig.run_tool("gmail_send", {"to": "a@b.cd", "subject": "x", "body": "y", "attachments": "relative.txt"})

    def test_thread_attachment_filters_vacation_drafts(self):
        thread = ig.run_tool("gmail_read_thread", {"thread_id": "t1"})
        self.assertEqual(thread["count"], 1)
        folder = self.tmp / "save"
        self.fake.messages["m1"]["_files"] = True
        with patch.object(self.fake, "_msg", wraps=self.fake._msg) as wrapped:
            def with_file(m, full):
                row = Fake33._msg(self.fake, m, full)
                if full:
                    row["payload"]["parts"].append({"filename": "a.txt", "mimeType": "text/plain", "body": {"attachmentId": "att1", "size": 5}})
                return row
            wrapped.side_effect = with_file
            saved = ig.run_tool("gmail_save_attachment", {"message_id": "m1", "folder": str(folder)})
        self.assertEqual(Path(saved["saved"][0]["path"]).read_bytes(), b"hello")
        f = ig.run_tool("gmail_create_filter", {"from": "news@shop.com", "archive": True, "mark_read": True, "add_labels": "Работа"})
        self.assertEqual(self.last("POST", "/settings/filters"), {"criteria": {"from": "news@shop.com"},
                                                                 "action": {"addLabelIds": ["Label_1"], "removeLabelIds": ["INBOX", "UNREAD"]}})
        self.assertEqual(len(ig.run_tool("gmail_list_filters", {})["filters"]), 1)
        ig.run_tool("gmail_delete_filter", {"filter_id": f["filter_id"]})
        ig.run_tool("gmail_vacation", {"enable": True, "subject": "Отпуск", "body": "Вернусь 20-го", "end": "2026-10-20"})
        body = self.last("PUT", "/settings/vacation")
        self.assertEqual((body["enableAutoReply"], body["responseBodyPlainText"]), (True, "Вернусь 20-го"))
        self.assertTrue(body["endTime"].isdigit())
        ig.run_tool("gmail_create_draft", {"to": "a@b.cd", "subject": "s", "body": "b"})
        self.assertEqual(ig.run_tool("gmail_list_drafts", {})["drafts"][0]["draft_id"], "d1")
        ig.run_tool("gmail_update_draft", {"draft_id": "d1", "to": "a@b.cd", "subject": "s2", "body": "b2"})
        self.assertEqual(self.last("PUT", "/drafts/d1")["id"], "d1")
        ig.run_tool("gmail_delete_draft", {"draft_id": "d1"})

    def test_every_listed_gmail_tool_is_dispatched(self):
        for line in integrations_gmail.TOOLS:
            name = line.split("{")[0]
            try:
                ig.run_tool(name, {})
            except ig.IntegrationError as exc:
                self.assertNotIn("Неизвестный инструмент", str(exc), name)


class ScopesAndApisTest(Base):
    def test_old_grant_is_ready_but_limited_and_expanding_reconsents(self):
        old = {"openid", G + "userinfo.email", G + "gmail.readonly", G + "gmail.compose", G + "drive.readonly", G + "drive.file"}
        self.sign_in(("gmail", "drive"), granted=old)
        rows = {row["id"]: row for row in google_account.status()["services"]}
        self.assertTrue(rows["gmail"]["ready"])
        self.assertFalse(rows["gmail"]["full"])
        self.assertTrue(ig.run_tool("gmail_search", {})["messages"], "reading still works with the old grant")
        before = len(self.fake.calls)
        with self.assertRaises(ig.IntegrationAccountError) as caught:
            ig.run_tool("gmail_trash", {"message_ids": ["m1"]})
        self.assertIn("Расширить доступ", str(caught.exception))
        with self.assertRaises(ig.IntegrationAccountError):
            ig.run_tool("drive_rename", {"file_id": "f9", "name": "x"})
        self.assertEqual(len(self.fake.calls), before, "the scope check runs before any request")
        # «Expand access» = enable again → Google's page for the grown scopes (incremental).
        result = google_account.set_enabled("gmail", True)
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(result["auth_url"]).query))
        self.assertIn("https://mail.google.com/", params["scope"].split())
        self.assertEqual(params["include_granted_scopes"], "true")
        self.fake.granted |= set(params["scope"].split())
        visit(f"{params['redirect_uri']}?code=CODE&state={urllib.parse.quote(params['state'])}")
        self.assertTrue({row["id"]: row for row in google_account.status()["services"]}["gmail"]["full"])
        ig.run_tool("gmail_trash", {"message_ids": ["m1"]})

    def test_disabled_api_says_which_and_links_to_the_project(self):
        self.sign_in()
        self.fake.disabled = {"gmail.googleapis.com", "docs.googleapis.com"}
        with self.assertRaises(ig.IntegrationError) as caught:
            ig.run_tool("gmail_search", {"query": "x"})
        text = str(caught.exception)
        self.assertIn("Gmail API выключен", text)
        self.assertIn("https://console.developers.google.com/apis/api/gmail.googleapis.com/overview?project=123", text)
        check = ig.google_check()
        states = {row["id"]: row["state"] for row in check["services"]}
        self.assertEqual(states["gmail"], "disabled")
        self.assertEqual(states["docs"], "disabled")
        self.assertEqual(states["sheets"], "ok", "a made-up id answering 404 means the API is on")
        self.assertEqual(states["calendar"], "ok")
        self.assertIn("apiid=gmail.googleapis.com,docs.googleapis.com", check["enable_all_url"])
        self.assertIn("project=123456789012", check["enable_all_url"], "the project number comes from the Client ID")
        gmail_row = next(r for r in check["services"] if r["id"] == "gmail")
        self.assertIn("gmail.googleapis.com", gmail_row["enable_url"])
        # The error stays visible in Settings until the API answers again.
        self.assertIn("Gmail API", {r["service"]: r for r in ig.status()["services"]}["gmail"]["last_error"])
        self.fake.disabled = set()
        ig.google_check()
        self.assertEqual({r["service"]: r for r in ig.status()["services"]}["gmail"]["last_error"], "")

    def test_setup_links(self):
        self.assertEqual(google_account.project_number("987654321-xyz.apps.googleusercontent.com"), "987654321")
        self.assertEqual(google_account.project_number("abc.apps.googleusercontent.com"), "")
        url = google_account.enable_url(project="42")
        for api in ("gmail.googleapis.com", "calendar-json.googleapis.com", "drive.googleapis.com", "docs.googleapis.com",
                    "sheets.googleapis.com", "tasks.googleapis.com", "people.googleapis.com"):
            self.assertIn(api, url)
        self.assertTrue(url.endswith("&project=42"))
        google_account.save_client("123456789012-abc.apps.googleusercontent.com", "GOCSPX-secret")
        client = google_account.status()["client"]
        self.assertEqual(client["project"], "123456789012")
        self.assertIn("project=123456789012", client["enable_all_url"])


class DriveCalendarWorkspaceTest(Base):
    def setUp(self) -> None:
        super().setUp()
        self.sign_in()

    def test_drive_rename_move_copy_trash_delete_share_download(self):
        ig.run_tool("drive_rename", {"file_id": "f9", "name": "Новое имя.txt"})
        self.assertEqual(self.last("PATCH", "/files/f9"), {"name": "Новое имя.txt"})
        ig.run_tool("drive_move", {"file_id": "f9", "folder_id": "fold"})
        q = self.query("/files/f9")
        self.assertEqual((q["addParents"], q["removeParents"]), ("fold", "root"))
        copy = ig.run_tool("drive_copy", {"file_id": "f9", "name": "Копия"})
        self.assertEqual(self.last("POST", "/files/f9/copy"), {"name": "Копия"})
        ig.run_tool("drive_trash", {"file_id": copy["id"]})
        self.assertEqual(self.last("PATCH", "/files/" + copy["id"]), {"trashed": True})
        ig.run_tool("drive_delete_file", {"file_id": copy["id"]})
        self.assertNotIn(copy["id"], self.fake.files)
        ig.run_tool("drive_share", {"file_id": "f9", "email": "anna@example.com", "role": "editor", "message": "Держи"})
        self.assertEqual(self.last("POST", "/files/f9/permissions"), {"type": "user", "role": "writer", "emailAddress": "anna@example.com"})
        self.assertEqual(self.query("/files/f9/permissions")["sendNotificationEmail"], "true")
        ig.run_tool("drive_unshare", {"file_id": "f9", "email": "anna@example.com"})
        self.assertEqual(self.fake.permissions["f9"], [])
        folder = ig.run_tool("drive_create_folder", {"name": "Проект"})
        self.assertEqual(self.last("POST", "/drive/v3/files"), {"name": "Проект", "mimeType": "application/vnd.google-apps.folder"})
        self.assertTrue(folder["created"])
        saved = ig.run_tool("drive_download", {"file_id": "f1", "format": "pdf", "folder": str(self.tmp / "dl")})
        self.assertTrue(saved["path"].endswith("Отчёт Q3.pdf"))
        self.assertEqual(self.query("/files/f1/export")["mimeType"], "application/pdf")
        self.assertTrue(Path(saved["path"]).exists())
        listed = ig.run_tool("drive_list_files", {"mime": "folder", "trashed": True})
        self.assertIn("mimeType = 'application/vnd.google-apps.folder'", self.query("/drive/v3/files")["q"])
        self.assertIn("trashed = true", self.query("/drive/v3/files")["q"])
        self.assertIn("files", listed)

    def test_calendar_guests_meet_respond_freebusy(self):
        ig.run_tool("calendar_create_event", {"summary": "Созвон", "start": "2026-10-16T13:00", "end": "2026-10-16T14:00",
                                              "attendees": "anna@example.com, boris@example.com", "meet": True, "reminders": [10]})
        body = self.last("POST", "/events")
        self.assertEqual(body["attendees"], [{"email": "anna@example.com"}, {"email": "boris@example.com"}])
        self.assertEqual(body["conferenceData"]["createRequest"]["conferenceSolutionKey"], {"type": "hangoutsMeet"})
        self.assertEqual(body["reminders"], {"useDefault": False, "overrides": [{"method": "popup", "minutes": 10}]})
        q = self.query("/events")
        self.assertEqual((q["conferenceDataVersion"], q["sendUpdates"]), ("1", "all"))
        with self.assertRaises(ig.IntegrationArgumentError):
            ig.run_tool("calendar_create_event", {"summary": "x", "start": "2026-10-16T13:00", "end": "2026-10-16T14:00", "attendees": "nope"})
        self.fake.events[0]["attendees"] = [{"email": "me@example.com", "self": True, "responseStatus": "needsAction"}]
        ig.run_tool("calendar_respond", {"event_id": "e1", "response": "accept"})
        self.assertEqual(self.last("PATCH", "/events/e1")["attendees"][0]["responseStatus"], "accepted")
        ig.run_tool("calendar_list_events", {"time_min": "2026-10-01", "time_max": "2026-10-31"})
        q = self.query("/events")
        self.assertTrue(q["timeMin"].startswith("2026-10-01T00:00:00"))
        self.assertTrue(q["timeMax"].startswith("2026-11-01T00:00:00"), "a date as the end = the whole day")

    def test_docs_sheets_tasks_contacts(self):
        doc = ig.run_tool("gdocs_create", {"title": "План", "content": "Привет"})
        self.assertEqual(self.last("POST", "/documents"), {"title": "План"})
        self.assertEqual(self.last("POST", "/documents/doc1234567890:batchUpdate"),
                         {"requests": [{"insertText": {"location": {"index": 1}, "text": "Привет"}}]})
        self.assertEqual(ig.run_tool("gdocs_read", {"document_id": "https://docs.google.com/document/d/doc1234567890/edit"})["text"],
                         "# План\n- Пункт один")
        self.assertEqual(ig.run_tool("gdocs_replace", {"document_id": doc["id"], "find": "a", "replace": "b"})["replaced"], 2)
        sheet = ig.run_tool("gsheets_read", {"spreadsheet_id": "sheet1234567890"})
        self.assertEqual(sheet["values"][1], ["Анна", "120"])
        ig.run_tool("gsheets_append", {"spreadsheet_id": "sheet1234567890", "range": "Лист1", "values": [["Борис", 80]]})
        self.assertEqual(self.last("POST", ":append"), {"values": [["Борис", 80]]})
        self.assertEqual(self.query(":append")["valueInputOption"], "USER_ENTERED")
        ig.run_tool("gsheets_write", {"spreadsheet_id": "sheet1234567890", "range": "Лист1!C1", "values": "[[\"=SUM(B2:B3)\"]]"})
        self.assertEqual(self.last("PUT", "C1")["values"], [["=SUM(B2:B3)"]])
        task = ig.run_tool("gtasks_create", {"title": "Позвонить", "due": "2026-10-16"})
        self.assertEqual(self.last("POST", "/tasks"), {"title": "Позвонить", "notes": "", "due": "2026-10-16T00:00:00.000Z"})
        ig.run_tool("gtasks_complete", {"task_id": task["id"]})
        self.assertEqual(self.last("PATCH", "/tasks/" + task["id"]), {"status": "completed"})
        found = ig.run_tool("gcontacts_search", {"query": "анна"})
        self.assertEqual(found["contacts"][0]["emails"], ["anna@example.com"])
        ig.run_tool("gcontacts_update", {"resource_name": "people/c1", "phone": "+79990000000"})
        self.assertEqual(self.query(":updateContact")["updatePersonFields"], "phoneNumbers")
        self.assertEqual(self.last("PATCH", "people/c1:updateContact")["etag"], "e1")

    def test_every_google_tool_line_is_dispatched(self):
        for service in ("google_drive", "google_calendar", "google_docs", "google_sheets", "google_tasks", "google_contacts"):
            for line in ig.TOOLS[service]:
                name = line.split("{")[0]
                if name in ("drive_upload_file",):
                    continue
                try:
                    ig.run_tool(name, {})
                except ig.IntegrationError as exc:
                    self.assertNotIn("Неизвестный инструмент", str(exc), name)


class NotionPropertiesTest(unittest.TestCase):
    def test_plain_values_are_shaped_by_the_schema(self):
        schema = {"properties": {"Name": {"type": "title"}, "Status": {"type": "status"}, "Due": {"type": "date"},
                                 "Tags": {"type": "multi_select"}, "Done": {"type": "checkbox"}, "Cost": {"type": "number"},
                                 "Formula": {"type": "formula"}}}
        out = integrations_notion_tools.simple_properties(schema, {"status": "Готово", "Due": "2026-10-16/2026-10-18",
                                                                   "Tags": "a, b", "Done": "да", "Cost": "12,5", "Formula": 1,
                                                                   "Raw": {"rich_text": []}})
        self.assertEqual(out["Status"], {"status": {"name": "Готово"}})
        self.assertEqual(out["Due"], {"date": {"start": "2026-10-16", "end": "2026-10-18"}})
        self.assertEqual(out["Tags"], {"multi_select": [{"name": "a"}, {"name": "b"}]})
        self.assertEqual(out["Done"], {"checkbox": True})
        self.assertEqual(out["Cost"], {"number": 12.5})
        self.assertNotIn("Formula", out, "read-only properties are skipped instead of failing the request")
        self.assertEqual(out["Raw"], {"rich_text": []}, "raw Notion JSON passes through")


class WiringTest(unittest.TestCase):
    def test_ui_sets_and_cards_match_the_server(self):
        ui = (ROOT / "app/pgstatic/integrations-ui.js").read_text(encoding="utf-8")
        start = ui.index("const DANGER_TOOLS = new Set([")
        danger = set(re.findall(r'"(\w+)"', ui[start:ui.index("]);", start)]))
        self.assertEqual(danger, ig.DANGER_TOOLS)
        self.assertTrue(ig.ALWAYS_CONFIRM <= ig.WRITE_TOOLS)
        for name in ig.WRITE_TOOLS:
            self.assertRegex(ui, r"\b%s: \[" % name, "a confirmation card for " + name)
        self.assertIn("Use this client for an AI-powered agent", ui)
        for api in ("gmail.googleapis.com", "calendar-json.googleapis.com", "drive.googleapis.com", "docs.googleapis.com",
                    "sheets.googleapis.com", "tasks.googleapis.com", "people.googleapis.com"):
            self.assertIn(api, ui)
        api = (ROOT / "app/playground_api.py").read_text(encoding="utf-8")
        line = next(row for row in api.splitlines() if 'tool.startswith(("github_"' in row)
        for prefix in ig.TOOL_PREFIXES:
            self.assertIn('"%s"' % prefix, line)

    def test_status_carries_hints_and_tool_sets(self):
        data = ig.status()
        rows = {row["service"]: row for row in data["services"]}
        self.assertIn("github_delete_repo", data["always_confirm"])
        self.assertIn("gmail_trash", data["danger_tools"])
        self.assertIn("PERMANENT", rows["gmail"]["hint"])
        self.assertIn("gmail_delete{message_ids,thread_id}", rows["gmail"]["tools"])
        self.assertIn("github_delete_file{repo,path,message,branch}", rows["github"]["tools"])
        names = [t.split("{")[0] for tools in ig.TOOLS.values() for t in tools]
        self.assertEqual(len(names), len(set(names)), "no duplicate tool names")

    def test_check_route(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import integrations_api
        app = FastAPI()
        integrations_api.register(app)
        with patch.object(ig, "google_check", return_value={"services": [], "enable_all_url": "", "checked": 1}):
            data = TestClient(app).post("/api/pg/integrations/google", json={"action": "check"}).json()
        self.assertEqual(data["check"]["checked"], 1)


if __name__ == "__main__":
    unittest.main()
