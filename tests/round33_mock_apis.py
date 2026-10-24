"""Round 33: an in-memory fake of the GitHub REST API and of the rest of Google (Gmail labels/trash/filters, Drive
rename/move/share, Docs, Sheets, Tasks, People) on top of tests/round30_mock_apis.FakeApis.  No network.

Request shapes follow the public docs (docs.github.com/rest, developers.google.com/gmail/api/reference/rest, …):
every call is recorded in ``.calls`` as ``(method, url-without-query, json-body-or-None)`` and ``.queries`` keeps the
query parameters, so tests assert *what MemuAI sent*.  ``disabled`` = a set of Google API ids
(``gmail.googleapis.com``…) answering 403 SERVICE_DISABLED exactly like Google does.
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any, Dict, List, Optional

import httpx

from round30_mock_apis import FakeApis, G, install  # noqa: F401  (install re-exported)

GH = "https://api.github.com"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
HOST_API = {"gmail.googleapis.com": "gmail.googleapis.com", "docs.googleapis.com": "docs.googleapis.com",
            "sheets.googleapis.com": "sheets.googleapis.com", "tasks.googleapis.com": "tasks.googleapis.com",
            "people.googleapis.com": "people.googleapis.com"}
API_TITLE = {"gmail.googleapis.com": "Gmail API", "drive.googleapis.com": "Google Drive API",
             "calendar-json.googleapis.com": "Google Calendar API", "docs.googleapis.com": "Google Docs API",
             "sheets.googleapis.com": "Google Sheets API", "tasks.googleapis.com": "Google Tasks API",
             "people.googleapis.com": "People API"}


def _api_of(url: str) -> str:
    host = url.split("/")[2]
    if host in HOST_API:
        return HOST_API[host]
    if "/drive/v3" in url:
        return "drive.googleapis.com"
    if "/calendar/v3" in url:
        return "calendar-json.googleapis.com"
    return ""


def disabled_error(api: str, project: str = "123") -> httpx.Response:
    url = "https://console.developers.google.com/apis/api/%s/overview?project=%s" % (api, project)
    title = API_TITLE.get(api, api)
    message = ("%s has not been used in project %s before or it is disabled. Enable it by visiting %s then retry. "
               "If you enabled this API recently, wait a few minutes for the action to propagate to our systems and retry."
               % (title, project, url))
    return httpx.Response(403, json={"error": {
        "code": 403, "message": message, "status": "PERMISSION_DENIED",
        "errors": [{"message": message, "domain": "usageLimits", "reason": "accessNotConfigured", "extendedHelp": "https://console.developers.google.com"}],
        "details": [
            {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "SERVICE_DISABLED", "domain": "googleapis.com",
             "metadata": {"service": api, "consumer": "projects/" + project, "serviceTitle": title,
                          "containerInfo": project, "activationUrl": url}},
            {"@type": "type.googleapis.com/google.rpc.LocalizedMessage", "locale": "en-US", "message": message},
            {"@type": "type.googleapis.com/google.rpc.Help", "links": [{"description": "Google developers console API activation", "url": url}]},
        ]}})


class Fake33(FakeApis):
    def __init__(self) -> None:
        super().__init__()
        self.queries: List[Dict[str, str]] = []
        self.disabled: set = set()
        self.github_token = "ghp_" + "x" * 36
        self.github_scopes = "repo, workflow, gist, delete_repo"
        self.github_403: Dict[str, str] = {}          # path → "fine" | "classic": answer 403 with the permission header
        self.repos: Dict[str, Dict[str, Any]] = {
            "me/notes": {"files": {"README.md": "# Notes\n", "docs/a.md": "A", "docs/b.md": "B"}, "default_branch": "main",
                         "private": True, "description": "My notes", "branches": {"main": "c0"}, "issues": {}, "labels": ["bug"],
                         "releases": [], "collaborators": []},
        }
        self.commits = 0
        self.gists: Dict[str, Dict[str, Any]] = {}
        self.pulls: Dict[int, Dict[str, Any]] = {}
        self.labels = [{"id": "INBOX", "name": "INBOX", "type": "system"}, {"id": "UNREAD", "name": "UNREAD", "type": "system"},
                       {"id": "Label_1", "name": "Работа", "type": "user"}]
        self.filters: List[Dict[str, Any]] = []
        self.deleted_mail: List[str] = []
        self.files.update({"f9": {"id": "f9", "name": "Заметки.txt", "mimeType": "text/plain", "parents": ["root"]},
                           "fold": {"id": "fold", "name": "Архив", "mimeType": "application/vnd.google-apps.folder", "parents": ["root"]}})
        self.permissions: Dict[str, List[Dict[str, Any]]] = {}
        self.tasks: List[Dict[str, Any]] = [{"id": "t1", "title": "Купить молоко", "status": "needsAction"}]
        self.contacts: List[Dict[str, Any]] = [{"resourceName": "people/c1", "etag": "e1", "names": [{"displayName": "Анна Петрова"}],
                                                "emailAddresses": [{"value": "anna@example.com"}]}]
        self.sheet_values: Dict[str, List[List[Any]]] = {"'Лист1'!A1:Z200": [["Имя", "Сумма"], ["Анна", "120"]]}
        self._pending: Dict[str, List[Dict[str, Any]]] = {}
        self._trees: Dict[str, List[Dict[str, Any]]] = {}

    # ------------------------------------------------------------------ handler
    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        self.queries.append(dict(request.url.params))
        if url.startswith(GH):
            body = self._json(request)
            self.calls.append((request.method, url, body))
            return self._github(request, url[len(GH):], body)
        api = _api_of(url)
        if api and api in self.disabled and request.headers.get("authorization") == "Bearer " + self.access:
            self.calls.append((request.method, url, self._json(request)))
            return disabled_error(api)
        extra = self._google_extra(request, url)
        if extra is not None:
            return extra
        self.queries.pop()          # the base handler records its own call; keep the two lists aligned
        response = super().handler(request)
        self.queries.append(dict(request.url.params))
        return response

    # ------------------------------------------------------------------ GitHub
    def _gh_json(self, status: int, data: Any, headers: Optional[Dict[str, str]] = None) -> httpx.Response:
        h = {"x-oauth-scopes": self.github_scopes} if self.github_scopes is not None else {}
        h.update(headers or {})
        if status == 204:
            return httpx.Response(204, headers=h)
        return httpx.Response(status, json=data, headers=h)

    def _github(self, request: httpx.Request, path: str, body: Any) -> httpx.Response:
        m = request.method
        if request.headers.get("authorization") != "Bearer " + self.github_token:
            return self._gh_json(401, {"message": "Bad credentials"})
        for prefix, kind in self.github_403.items():
            if path.startswith(prefix):
                head = {"x-accepted-github-permissions": "administration=write"} if kind == "fine" else {"x-accepted-oauth-scopes": "delete_repo"}
                return self._gh_json(403, {"message": "Resource not accessible by personal access token"}, head)
        if path == "/user":
            return self._gh_json(200, {"login": "me", "name": "Me", "public_repos": 3, "total_private_repos": 2})
        org = re.match(r"/orgs/([^/]+)/repos$", path)
        if (path == "/user/repos" or org) and m == "POST":
            full = (org.group(1) if org else "me") + "/" + body["name"]
            self.repos[full] = {"files": {"README.md": "# %s\n" % body["name"]} if body.get("auto_init") else {}, "default_branch": "main",
                                "private": body.get("private"), "description": body.get("description"), "branches": {"main": "c0"},
                                "issues": {}, "labels": [], "releases": [], "collaborators": []}
            return self._gh_json(201, self._repo(full))
        if path == "/user/repos":
            return self._gh_json(200, [self._repo(name) for name in self.repos])
        if path.startswith("/search/repositories"):
            return self._gh_json(200, {"total_count": 1, "items": [self._repo("me/notes")]})
        if path.startswith("/search/issues"):
            return self._gh_json(200, {"total_count": 0, "items": []})
        if path == "/gists" and m == "POST":
            gid = "g%d" % (len(self.gists) + 1)
            self.gists[gid] = {"id": gid, "description": body.get("description"), "public": body.get("public"),
                               "files": {k: {"content": v["content"]} for k, v in body["files"].items()}, "html_url": "https://gist.github.com/" + gid}
            return self._gh_json(201, self.gists[gid])
        if path == "/gists":
            return self._gh_json(200, list(self.gists.values()))
        gm = re.match(r"/gists/([^/]+)$", path)
        if gm:
            gid = gm.group(1)
            if gid not in self.gists:
                return self._gh_json(404, {"message": "Not Found"})
            if m == "DELETE":
                del self.gists[gid]
                return self._gh_json(204, None)
            if m == "PATCH":
                for name, value in (body.get("files") or {}).items():
                    if value is None:
                        self.gists[gid]["files"].pop(name, None)
                    else:
                        self.gists[gid]["files"][name] = {"content": value.get("content")}
                return self._gh_json(200, self.gists[gid])
            return self._gh_json(200, self.gists[gid])
        rm = re.match(r"/repos/([^/]+/[^/]+)(/.*)?$", path)
        if not rm:
            return self._gh_json(404, {"message": "Not Found"})
        full, rest = rm.group(1), rm.group(2) or ""
        if rest.startswith("/generate"):
            name = "me/" + body["name"]
            self.repos[name] = dict(self.repos.get(full) or {}, files=dict((self.repos.get(full) or {}).get("files") or {}))
            return self._gh_json(201, self._repo(name))
        repo = self.repos.get(full)
        if repo is None:
            return self._gh_json(404, {"message": "Not Found"})
        if rest == "":
            if m == "DELETE":
                del self.repos[full]
                return self._gh_json(204, None)
            if m == "PATCH":
                if body.get("name"):
                    new = full.split("/")[0] + "/" + body["name"]
                    self.repos[new] = self.repos.pop(full)
                    full, repo = new, self.repos[new]
                for key in ("description", "private", "default_branch"):
                    if key in body:
                        repo[key] = body[key]
            return self._gh_json(200, self._repo(full))
        if rest == "/topics":
            return self._gh_json(200, {"names": body.get("names")})
        if rest == "/forks":
            fork = (body.get("organization") or "me") + "/" + (body.get("name") or full.split("/")[1])
            self.repos[fork] = dict(repo, files=dict(repo["files"]))
            return self._gh_json(202, self._repo(fork))
        cm = re.match(r"/contents/(.+)$", rest)
        if cm:
            fpath = cm.group(1)
            if m == "GET":
                if fpath in repo["files"]:
                    text = repo["files"][fpath]
                    return self._gh_json(200, {"type": "file", "path": fpath, "sha": "sha-" + fpath, "size": len(text),
                                               "encoding": "base64", "content": base64.b64encode(text.encode()).decode()})
                inside = [p for p in repo["files"] if p.startswith(fpath + "/")]
                if inside:
                    return self._gh_json(200, [{"type": "file", "path": p, "name": p.split("/")[-1], "sha": "sha-" + p} for p in inside])
                return self._gh_json(404, {"message": "Not Found"})
            if m == "PUT":
                repo["files"][fpath] = base64.b64decode(body["content"]).decode("utf-8", "replace")
                self.commits += 1
                return self._gh_json(200, {"content": {"path": fpath, "html_url": "https://github.com/%s/blob/main/%s" % (full, fpath)},
                                           "commit": {"sha": "c%d" % self.commits}})
            if m == "DELETE":
                if body.get("sha") != "sha-" + fpath:
                    return self._gh_json(409, {"message": "sha mismatch"})
                repo["files"].pop(fpath, None)
                self.commits += 1
                return self._gh_json(200, {"commit": {"sha": "c%d" % self.commits}})
        ref = re.match(r"/git/refs?/heads/(.+)$", rest)
        if ref:
            branch = ref.group(1)
            if m == "GET":
                if branch not in repo["branches"]:
                    return self._gh_json(404, {"message": "Not Found"})
                return self._gh_json(200, {"ref": "refs/heads/" + branch, "object": {"sha": repo["branches"][branch]}})
            if m == "PATCH":
                repo["branches"][branch] = body["sha"]
                for entry in self._pending.pop(body["sha"], []):
                    if entry.get("sha", "x") is None:
                        repo["files"].pop(entry["path"], None)
                    else:
                        repo["files"][entry["path"]] = entry.get("content", repo["files"].get(entry.get("_from", ""), ""))
                return self._gh_json(200, {"object": {"sha": body["sha"]}})
            if m == "DELETE":
                repo["branches"].pop(branch, None)
                return self._gh_json(204, None)
        if rest == "/git/refs" and m == "POST":
            repo["branches"][body["ref"].split("refs/heads/")[1]] = body["sha"]
            return self._gh_json(201, {"ref": body["ref"], "object": {"sha": body["sha"]}})
        if re.match(r"/git/commits/[^/]+$", rest) and m == "GET":
            return self._gh_json(200, {"sha": rest.rsplit("/", 1)[1], "tree": {"sha": "tree-" + rest.rsplit("/", 1)[1]}})
        if re.match(r"/git/trees/[^/]+$", rest) and m == "GET":
            return self._gh_json(200, {"truncated": False, "tree": [{"path": p, "mode": "100644", "type": "blob", "sha": "blob-" + p}
                                                                     for p in repo["files"]]})
        if rest == "/git/trees" and m == "POST":
            entries = []
            for entry in body["tree"]:
                row = dict(entry)
                if row.get("sha") and str(row["sha"]).startswith("blob-"):
                    row["_from"] = str(row["sha"])[5:]
                    row["content"] = repo["files"].get(row["_from"], "")
                entries.append(row)
            tree_sha = "tree%d" % (len(self.calls))
            self._trees[tree_sha] = entries
            return self._gh_json(201, {"sha": tree_sha})
        if rest == "/git/commits" and m == "POST":
            self.commits += 1
            sha = "c%d" % self.commits
            self._pending[sha] = self._trees.get(body["tree"], [])
            return self._gh_json(201, {"sha": sha, "html_url": "https://github.com/%s/commit/%s" % (full, sha)})
        if rest == "/git/blobs" and m == "POST":
            return self._gh_json(201, {"sha": "rawblob"})
        if rest.startswith("/branches"):
            return self._gh_json(200, [{"name": b, "commit": {"sha": s}, "protected": False} for b, s in repo["branches"].items()])
        if re.match(r"/commits/[^/]+$", rest):
            return self._gh_json(200, {"sha": "c0" * 20, "commit": {"message": "init", "author": {"name": "me", "date": "2026-10-01"}},
                                       "stats": {"total": 1}, "files": [{"filename": "README.md", "status": "added", "additions": 1, "deletions": 0, "patch": "+# Notes"}]})
        if rest == "/issues" and m == "POST":
            number = len(repo["issues"]) + 1
            repo["issues"][number] = {"number": number, "title": body["title"], "state": "open", "labels": [{"name": n} for n in body.get("labels") or []],
                                      "assignees": [{"login": a} for a in body.get("assignees") or []], "html_url": "https://github.com/%s/issues/%d" % (full, number)}
            return self._gh_json(201, repo["issues"][number])
        im = re.match(r"/issues/(\d+)(/.*)?$", rest)
        if im:
            number, tail = int(im.group(1)), im.group(2) or ""
            issue = repo["issues"].get(number) or {"number": number, "title": "x", "state": "open", "labels": [], "assignees": []}
            repo["issues"][number] = issue
            if tail == "" and m == "PATCH":
                issue.update({k: v for k, v in body.items() if k in ("title", "state", "body")})
                if "labels" in body:
                    issue["labels"] = [{"name": n} for n in body["labels"]]
                return self._gh_json(200, issue)
            if tail == "/comments" and m == "POST":
                return self._gh_json(201, {"id": 77, "html_url": "https://github.com/%s/issues/%d#c77" % (full, number)})
            if tail == "/comments":
                return self._gh_json(200, [])
            if tail == "/labels" and m == "POST":
                issue["labels"] += [{"name": n} for n in body["labels"]]
                return self._gh_json(200, issue["labels"])
            if tail.startswith("/labels/") and m == "DELETE":
                name = tail.split("/labels/")[1]
                issue["labels"] = [lab for lab in issue["labels"] if lab["name"] != httpx.URL("http://x/" + name).path[1:]]
                return self._gh_json(200, issue["labels"])
            if tail == "/assignees":
                return self._gh_json(201, {"assignees": [{"login": a} for a in body["assignees"]]})
            return self._gh_json(200, issue)
        if rest == "/labels" and m == "POST":
            return self._gh_json(201, {"name": body["name"], "color": body["color"]})
        if rest.startswith("/labels"):
            return self._gh_json(200, [{"name": n, "color": "ff0000"} for n in repo["labels"]])
        if rest == "/pulls" and m == "POST":
            number = 100 + len(self.pulls) + 1
            self.pulls[number] = {"number": number, "title": body["title"], "state": "open", "head": {"ref": body["head"]},
                                  "base": {"ref": body["base"]}, "draft": body.get("draft"), "html_url": "https://github.com/%s/pull/%d" % (full, number)}
            return self._gh_json(201, self.pulls[number])
        if rest.startswith("/pulls"):
            pm = re.match(r"/pulls/(\d+)(/.*)?$", rest)
            if pm and pm.group(2) == "/merge":
                return self._gh_json(200, {"merged": True, "sha": "m" * 40, "message": "Pull Request successfully merged"})
            if pm and pm.group(2) == "/reviews":
                return self._gh_json(200, {"id": 5, "state": "APPROVED" if body["event"] == "APPROVE" else body["event"]})
            if pm and pm.group(2) == "/files":
                return self._gh_json(200, [{"filename": "a.py", "status": "modified", "additions": 1, "deletions": 1, "patch": "-a\n+b"}])
            if pm and pm.group(2) == "/requested_reviewers":
                return self._gh_json(201, {"requested_reviewers": [{"login": r} for r in body["reviewers"]]})
            if pm:
                return self._gh_json(200, self.pulls.get(int(pm.group(1)), {"number": int(pm.group(1)), "title": "PR"}))
            return self._gh_json(200, list(self.pulls.values()))
        if rest == "/releases" and m == "POST":
            row = {"id": 900 + len(repo["releases"]), "tag_name": body["tag_name"], "name": body.get("name"), "draft": body.get("draft"),
                   "html_url": "https://github.com/%s/releases/tag/%s" % (full, body["tag_name"])}
            repo["releases"].append(row)
            return self._gh_json(201, row)
        if rest.startswith("/releases/tags/"):
            tag = rest.split("/releases/tags/")[1]
            row = next((r for r in repo["releases"] if r["tag_name"] == tag), None)
            return self._gh_json(200, row) if row else self._gh_json(404, {"message": "Not Found"})
        if re.match(r"/releases/\d+$", rest) and m == "DELETE":
            repo["releases"] = [r for r in repo["releases"] if str(r["id"]) != rest.rsplit("/", 1)[1]]
            return self._gh_json(204, None)
        if rest.startswith("/releases"):
            return self._gh_json(200, repo["releases"])
        if rest.startswith("/git/refs/tags/") and m == "DELETE":
            return self._gh_json(204, None)
        if rest.startswith("/tags"):
            return self._gh_json(200, [{"name": "v1.0", "commit": {"sha": "c0"}}])
        if rest == "/actions/workflows":
            return self._gh_json(200, {"workflows": [{"id": 1, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active"}]})
        if re.match(r"/actions/(workflows/[^/]+/)?runs$", rest):
            return self._gh_json(200, {"total_count": 1, "workflow_runs": [{"id": 555, "name": "CI", "status": "completed", "conclusion": "failure",
                                                                             "head_branch": "main", "event": "push", "head_sha": "c0"}]})
        if re.match(r"/actions/runs/\d+$", rest):
            return self._gh_json(200, {"id": 555, "name": "CI", "status": "completed", "conclusion": "failure", "head_branch": "main"})
        if re.match(r"/actions/runs/\d+/jobs$", rest):
            return self._gh_json(200, {"jobs": [{"id": 777, "name": "test", "status": "completed", "conclusion": "failure",
                                                 "steps": [{"name": "pytest", "conclusion": "failure"}]}]})
        if re.match(r"/actions/jobs/\d+/logs$", rest):
            return httpx.Response(200, content=("line\n" * 10 + "FAILED test_x - AssertionError\n").encode(), headers={"content-type": "text/plain"})
        if re.match(r"/actions/runs/\d+/(rerun|rerun-failed-jobs|cancel)$", rest) and m == "POST":
            return self._gh_json(201, {})
        if re.match(r"/actions/workflows/[^/]+/dispatches$", rest) and m == "POST":
            return self._gh_json(204, None)
        if rest.startswith("/collaborators"):
            cm2 = re.match(r"/collaborators/([^/]+)$", rest)
            if cm2 and m == "PUT":
                return self._gh_json(201, {"id": 1, "html_url": "https://github.com/%s/invitations" % full})
            if cm2 and m == "DELETE":
                return self._gh_json(204, None)
            return self._gh_json(200, [{"login": "me", "role_name": "admin", "permissions": {"admin": True}}])
        if rest.startswith("/compare/"):
            return self._gh_json(200, {"status": "ahead", "ahead_by": 1, "behind_by": 0, "commits": [], "files": []})
        return self._gh_json(404, {"message": "Not Found: " + rest})

    def _repo(self, full: str) -> Dict[str, Any]:
        repo = self.repos[full]
        return {"full_name": full, "private": repo.get("private"), "default_branch": repo.get("default_branch", "main"),
                "description": repo.get("description"), "html_url": "https://github.com/" + full, "visibility": "private" if repo.get("private") else "public"}

    # ------------------------------------------------------------------ Google (beyond Round 30)
    def _google_extra(self, request: httpx.Request, url: str) -> Optional[httpx.Response]:
        m = request.method
        auth = request.headers.get("authorization", "")
        google = url.startswith(("https://gmail.googleapis.com", "https://www.googleapis.com", "https://docs.googleapis.com",
                                 "https://sheets.googleapis.com", "https://tasks.googleapis.com", "https://people.googleapis.com"))
        if not google or url.startswith("https://www.googleapis.com/upload") and m == "POST" and "/gmail/" not in url:
            return None
        if auth != "Bearer " + self.access:
            return None
        body = self._json(request)
        hit = self._route_google(m, url, body, request)
        if hit is not None:
            self.calls.append((m, url, body))
        return hit

    def _route_google(self, m: str, url: str, body: Any, request: httpx.Request) -> Optional[httpx.Response]:
        ok = lambda data, status=200: httpx.Response(status, json=data)  # noqa: E731
        if url.startswith(GMAIL):
            path = url[len(GMAIL):]
            if path == "/labels" and m == "GET":
                return ok({"labels": self.labels})
            if path == "/labels" and m == "POST":
                row = {"id": "Label_%d" % (len(self.labels) + 1), "name": body["name"], "type": "user"}
                self.labels.append(row)
                return ok(row)
            lm = re.match(r"/labels/([^/]+)$", path)
            if lm:
                if m == "DELETE":
                    self.labels = [r for r in self.labels if r["id"] != lm.group(1)]
                    return httpx.Response(204)
                for r in self.labels:
                    if r["id"] == lm.group(1):
                        r.update(body or {})
                        return ok(r)
            if path == "/messages/batchModify":
                for mid in body["ids"]:
                    msg = self.messages.get(mid)
                    if msg:
                        msg["labelIds"] = [l for l in msg["labelIds"] if l not in body.get("removeLabelIds", [])] + \
                                          [l for l in body.get("addLabelIds", []) if l not in msg["labelIds"]]
                return httpx.Response(204)
            if path == "/messages/batchDelete":
                for mid in body["ids"]:
                    self.messages.pop(mid, None)
                    self.deleted_mail.append(mid)
                return httpx.Response(204)
            tm = re.match(r"/messages/([^/]+)/(trash|untrash)$", path)
            if tm:
                msg = self.messages.get(tm.group(1))
                if not msg:
                    return ok({"error": {"code": 404, "message": "Not Found"}}, 404)
                if tm.group(2) == "trash":
                    msg["labelIds"] = [l for l in msg["labelIds"] if l != "INBOX"] + ["TRASH"]
                else:
                    msg["labelIds"] = [l for l in msg["labelIds"] if l != "TRASH"] + ["INBOX"]
                return ok({"id": msg["id"], "labelIds": msg["labelIds"]})
            am = re.match(r"/messages/([^/]+)/attachments/([^/]+)$", path)
            if am:
                return ok({"size": 5, "data": base64.urlsafe_b64encode(b"hello").decode().rstrip("=")})
            thm = re.match(r"/threads/([^/]+)(/(modify|trash|untrash))?$", path)
            if thm:
                tid = thm.group(1)
                rows = [msg for msg in self.messages.values() if msg["threadId"] == tid]
                if m == "GET":
                    return ok({"id": tid, "messages": [self._msg(r, True) for r in rows]})
                if m == "DELETE":
                    for r in rows:
                        self.messages.pop(r["id"], None)
                    return httpx.Response(204)
                return ok({"id": tid})
            if path == "/settings/filters" and m == "POST":
                row = dict(body, id="f%d" % (len(self.filters) + 1))
                self.filters.append(row)
                return ok(row)
            if path == "/settings/filters":
                return ok({"filter": self.filters})
            if path.startswith("/settings/filters/") and m == "DELETE":
                self.filters = [f for f in self.filters if f["id"] != path.rsplit("/", 1)[1]]
                return httpx.Response(204)
            if path == "/settings/vacation" and m == "PUT":
                return ok(body)
            if path == "/drafts" and m == "GET":
                return ok({"drafts": [{"id": "d%d" % (i + 1)} for i in range(len(self.drafts))]})
            dm = re.match(r"/drafts/(d\d+)$", path)
            if dm:
                if m == "DELETE":
                    return httpx.Response(204)
                if m == "PUT":
                    return ok({"id": dm.group(1), "message": {"id": "dmx"}})
                return ok({"id": dm.group(1), "message": self._msg(self.messages["m1"], False)})
            return None
        if url.startswith("https://gmail.googleapis.com/upload/gmail/v1/users/me/"):
            tail = url.rsplit("/users/me", 1)[1]
            if tail == "/messages/send":
                self.sent.append({"upload": True, "size": len(request.content)})
                return ok({"id": "s-up", "threadId": "ts"})
            if tail == "/drafts":
                self.drafts.append({"upload": True})
                return ok({"id": "d-up", "message": {"id": "dm-up"}})
        ev = re.match(r"https://www\.googleapis\.com/calendar/v3/calendars/[^/]+/events/([^/]+)$", url)
        if ev and m == "GET":
            row = next((e for e in self.events if e["id"] == ev.group(1)), None)
            return ok(row) if row else ok({"error": {"code": 404, "message": "Not Found"}}, 404)
        if url == "https://www.googleapis.com/calendar/v3/freeBusy":
            return ok({"calendars": {item["id"]: {"busy": []} for item in body["items"]}})
        drv = "https://www.googleapis.com/drive/v3"
        fm = re.match(re.escape(drv) + r"/files/([^/]+)$", url)
        if fm and m in ("PATCH", "DELETE"):
            fid = fm.group(1)
            row = self.files.get(fid)
            if not row:
                return ok({"error": {"code": 404, "message": "File not found: " + fid}}, 404)
            if m == "DELETE":
                del self.files[fid]
                return httpx.Response(204)
            q = dict(request.url.params)
            row.update({k: v for k, v in (body or {}).items() if k in ("name", "trashed")})
            if q.get("addParents"):
                row["parents"] = [p for p in row.get("parents", []) if p not in q.get("removeParents", "").split(",")] + [q["addParents"]]
            return ok(dict(row, webViewLink="https://drive.google.com/" + fid))
        if fm and m == "GET" and dict(request.url.params).get("fields") in ("parents", "webViewLink") or (
                fm and m == "GET" and "parents" in str(dict(request.url.params).get("fields", "")) and "alt" not in dict(request.url.params)):
            row = self.files.get(fm.group(1))
            if not row:
                return ok({"error": {"code": 404, "message": "File not found"}}, 404)
            return ok(dict(row, webViewLink="https://drive.google.com/" + row["id"]))
        if url == drv + "/files" and m == "POST":
            fid = "folder%d" % len(self.files)
            self.files[fid] = {"id": fid, "name": body["name"], "mimeType": body["mimeType"], "parents": body.get("parents", ["root"])}
            return ok(dict(self.files[fid], webViewLink="https://drive.google.com/" + fid))
        cm = re.match(re.escape(drv) + r"/files/([^/]+)/copy$", url)
        if cm:
            src = self.files[cm.group(1)]
            fid = "copy%d" % len(self.files)
            self.files[fid] = dict(src, id=fid, name=(body or {}).get("name") or "Копия " + src["name"])
            return ok(dict(self.files[fid], webViewLink="https://drive.google.com/" + fid))
        pm = re.match(re.escape(drv) + r"/files/([^/]+)/permissions(?:/([^/]+))?$", url)
        if pm:
            fid = pm.group(1)
            perms = self.permissions.setdefault(fid, [])
            if m == "POST":
                row = dict(body, id="p%d" % (len(perms) + 1))
                perms.append(row)
                return ok(row)
            if m == "DELETE":
                self.permissions[fid] = [p for p in perms if p["id"] != pm.group(2)]
                return httpx.Response(204)
            return ok({"permissions": perms})
        if url == drv + "/about" and "storageQuota" in str(dict(request.url.params).get("fields")):
            return ok({"user": {"emailAddress": "me@example.com"}, "storageQuota": {"limit": str(15 * 1024 ** 3), "usage": str(3 * 1024 ** 3)}})
        # Docs
        if url == "https://docs.googleapis.com/v1/documents" and m == "POST":
            return ok({"documentId": "doc1234567890", "title": body["title"]})
        dm2 = re.match(r"https://docs\.googleapis\.com/v1/documents/([^/:]+)(:batchUpdate)?$", url)
        if dm2:
            if dm2.group(1).startswith("memuai-api-check"):
                return ok({"error": {"code": 404, "message": "Requested entity was not found.", "status": "NOT_FOUND"}}, 404)
            if dm2.group(2):
                return ok({"replies": [{"replaceAllText": {"occurrencesChanged": 2}}] if "replaceAllText" in json.dumps(body) else [{}]})
            return ok({"title": "План", "body": {"content": [
                {"paragraph": {"paragraphStyle": {"namedStyleType": "HEADING_1"}, "elements": [{"textRun": {"content": "План\n"}}]}},
                {"paragraph": {"elements": [{"textRun": {"content": "Пункт один\n"}}], "bullet": {}}}]}})
        # Sheets
        if url == "https://sheets.googleapis.com/v4/spreadsheets" and m == "POST":
            return ok({"spreadsheetId": "sheet1234567890", "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/sheet1234567890",
                       "sheets": [{"properties": {"title": "Лист1", "sheetId": 0}}]})
        sm = re.match(r"https://sheets\.googleapis\.com/v4/spreadsheets/([^/:]+)(.*)$", url)
        if sm:
            if sm.group(1).startswith("memuai-api-check"):
                return ok({"error": {"code": 404, "message": "Requested entity was not found.", "status": "NOT_FOUND"}}, 404)
            rest = sm.group(2)
            if rest == "":
                return ok({"properties": {"title": "Бюджет"}, "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/" + sm.group(1),
                           "sheets": [{"properties": {"title": "Лист1", "sheetId": 0, "gridProperties": {"rowCount": 1000}}}]})
            if rest.startswith("/values/"):
                rng = httpx.URL("http://x" + rest[len("/values"):]).path[1:]
                if rng.endswith(":append"):
                    return ok({"updates": {"updatedRange": "Лист1!A3:B3", "updatedRows": len(body["values"])}})
                if rng.endswith(":clear"):
                    return ok({"clearedRange": rng[:-6]})
                if m == "PUT":
                    return ok({"updatedRange": rng, "updatedCells": sum(len(r) for r in body["values"])})
                return ok({"range": rng, "values": self.sheet_values.get(rng, [["Имя", "Сумма"], ["Анна", "120"]])})
            if rest == ":batchUpdate":
                return ok({"replies": [{"addSheet": {"properties": {"title": "Новый", "sheetId": 7}}}]})
        # Tasks
        if url.startswith("https://tasks.googleapis.com/tasks/v1"):
            path = url[len("https://tasks.googleapis.com/tasks/v1"):]
            if path == "/users/@me/lists":
                return ok({"items": [{"id": "@default", "title": "Мои задачи"}]} if m == "GET" else {"id": "L2", "title": body["title"]})
            tm2 = re.match(r"/lists/([^/]+)/tasks(?:/([^/]+))?$", path)
            if tm2:
                if m == "GET":
                    return ok({"items": self.tasks})
                if m == "POST":
                    row = dict(body, id="t%d" % (len(self.tasks) + 1), status="needsAction")
                    self.tasks.append(row)
                    return ok(row)
                row = next((t for t in self.tasks if t["id"] == tm2.group(2)), None)
                if not row:
                    return ok({"error": {"code": 404, "message": "Not found"}}, 404)
                if m == "DELETE":
                    self.tasks.remove(row)
                    return httpx.Response(204)
                row.update(body or {})
                return ok(row)
        # People
        if url.startswith("https://people.googleapis.com/v1"):
            path = url[len("https://people.googleapis.com/v1"):]
            if path == "/people:searchContacts":
                q = (dict(request.url.params).get("query") or "").lower()
                hits = [c for c in self.contacts if q and q in json.dumps(c, ensure_ascii=False).lower()]
                return ok({"results": [{"person": c} for c in hits]})
            if path == "/people/me/connections":
                return ok({"connections": self.contacts, "totalPeople": len(self.contacts)})
            if path == "/people:createContact":
                row = dict(body, resourceName="people/c%d" % (len(self.contacts) + 1), etag="e")
                self.contacts.append(row)
                return ok(row)
            pm2 = re.match(r"/(people/[^/:]+)(:updateContact|:deleteContact)?$", path)
            if pm2:
                row = next((c for c in self.contacts if c["resourceName"] == pm2.group(1)), None)
                if not row:
                    return ok({"error": {"code": 404, "message": "Not found"}}, 404)
                if pm2.group(2) == ":deleteContact":
                    self.contacts.remove(row)
                    return ok({})
                if pm2.group(2) == ":updateContact":
                    row.update({k: v for k, v in body.items() if k != "etag"})
                return ok(row)
        return None
