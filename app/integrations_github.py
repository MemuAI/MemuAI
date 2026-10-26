"""Round 33: the full GitHub tool set of the assistant (REST API v3, ``X-GitHub-Api-Version: 2022-11-28``).

Round 4–22 gave the assistant ten tools (read files, list commits/issues, create an issue, write one file).
People asked for the rest: deleting files, creating and deleting repositories, branches, pull requests, releases,
gists, Actions… This module adds them; :func:`app.integrations._github_tool` keeps the original ten and hands every
other ``github_*`` name to :func:`run`.

* HTTP, retries, the token and error texts are :func:`app.integrations._github` / ``_github_error`` (one place).
* Every tool that changes something is in :data:`WRITE` → the chat shows a confirmation card first
  (``integrations.WRITE_TOOLS``); irreversible ones (:data:`ALWAYS`) are confirmed even with «Full access».
* Multi-file commits, moves and renames go through the Git Data API (one commit, no half-applied state).

Token scopes (shown in Settings → Integrations → GitHub, see :data:`SCOPES_HELP`):
classic PAT — ``repo``, ``workflow``, ``gist``, ``delete_repo`` (+ ``read:org`` for organisation repositories);
fine-grained PAT — repository permissions Administration, Contents, Issues, Pull requests, Actions, Workflows
(Read and write), Metadata (Read); account permission Gists (Read and write).
"""
from __future__ import annotations

import base64
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

TOOLS: List[str] = [

    "github_create_repo{name,description,private,org,auto_init,gitignore,license,template}",
    "github_update_repo{repo,new_name,description,private,homepage,default_branch,has_issues,has_wiki,archived,topics}",
    "github_delete_repo{repo}",
    "github_fork_repo{repo,org,name,default_branch_only}",
    "github_search_repos{query,limit}",

    "github_list_branches{repo,limit}",
    "github_create_branch{repo,branch,from}",
    "github_delete_branch{repo,branch}",
    "github_get_commit{repo,sha}",
    "github_compare{repo,base,head}",
    "github_list_tags{repo,limit}",

    "github_delete_file{repo,path,message,branch}",
    "github_move_file{repo,path,new_path,message,branch}",
    "github_commit_files{repo,message,branch,files,delete}",

    "github_list_pulls{repo,state,base,head,limit}",
    "github_get_pull{repo,number}",
    "github_create_pull{repo,title,head,base,body,draft}",
    "github_update_pull{repo,number,title,body,state,base}",
    "github_merge_pull{repo,number,method,commit_title,commit_message}",
    "github_review_pull{repo,number,event,body}",
    "github_request_reviewers{repo,number,reviewers}",

    "github_get_issue{repo,number}",
    "github_update_issue{repo,number,title,body,state,state_reason,labels,assignees,milestone}",
    "github_comment{repo,number,body}",
    "github_list_comments{repo,number,limit}",
    "github_list_labels{repo}",
    "github_create_label{repo,name,color,description}",
    "github_add_labels{repo,number,labels}",
    "github_remove_label{repo,number,label}",
    "github_add_assignees{repo,number,assignees}",
    "github_search_issues{query,limit}",

    "github_list_releases{repo,limit}",
    "github_create_release{repo,tag,name,body,target,draft,prerelease,generate_notes}",
    "github_delete_release{repo,release_id,tag,delete_tag}",

    "github_list_gists{limit}",
    "github_read_gist{gist_id}",
    "github_create_gist{files,description,public}",
    "github_update_gist{gist_id,files,description}",
    "github_delete_gist{gist_id}",

    "github_list_workflows{repo}",
    "github_list_runs{repo,workflow,branch,status,limit}",
    "github_get_run{repo,run_id}",
    "github_job_logs{repo,job_id}",
    "github_rerun{repo,run_id,failed_only}",
    "github_cancel_run{repo,run_id}",
    "github_dispatch_workflow{repo,workflow,ref,inputs}",

    "github_list_collaborators{repo}",
    "github_add_collaborator{repo,username,permission}",
    "github_remove_collaborator{repo,username}",
]

WRITE = {
    "github_create_repo", "github_update_repo", "github_delete_repo", "github_fork_repo",
    "github_create_branch", "github_delete_branch",
    "github_delete_file", "github_move_file", "github_commit_files",
    "github_create_pull", "github_update_pull", "github_merge_pull", "github_review_pull", "github_request_reviewers",
    "github_update_issue", "github_comment", "github_create_label", "github_add_labels", "github_remove_label",
    "github_add_assignees",
    "github_create_release", "github_delete_release",
    "github_create_gist", "github_update_gist", "github_delete_gist",
    "github_rerun", "github_cancel_run", "github_dispatch_workflow",
    "github_add_collaborator", "github_remove_collaborator",
}

ALWAYS = {"github_delete_repo"}

DANGER = {"github_delete_repo", "github_delete_branch", "github_delete_file", "github_delete_release",
          "github_delete_gist", "github_remove_collaborator", "github_merge_pull"}

HINT = ("GitHub: repo is owner/name (or the default repository). Files: github_write_file (one file), "
        "github_commit_files{files:[{path,content}|{path,content_base64}],delete:[paths]} (many files, one commit), "
        "github_move_file (rename/move a file or folder), github_delete_file. Close or reopen an issue/PR with "
        "github_update_issue{state:'closed'|'open'}; PR comments use github_comment with the PR number. "
        "github_delete_repo is permanent: call it only when the user explicitly asked to delete that exact repository.")

MAX_PATCH = 4000
MAX_PATCH_TOTAL = 40000
MAX_LOG = 30000

def _ig():
    from . import integrations
    return integrations

def _gh(method: str, path: str, **kwargs: Any) -> Any:
    return _ig()._github(method, path, **kwargs)

def _arg(text: str):
    return _ig().IntegrationArgumentError(text)

def _repo(args: Dict[str, Any]) -> str:
    return _ig()._repo(args)

def _explicit_repo(args: Dict[str, Any]) -> str:
    """Destructive repository actions never fall back to the default repository."""
    if not str(args.get("repo") or "").strip():
        raise _arg("Укажите repo явно (owner/name) — для этого действия репозиторий по умолчанию не подставляется")
    return _ig()._parse_repo(str(args["repo"]))

def _int(value: Any, default: int, low: int, high: int) -> int:
    return _ig()._int(value, default, low, high)

def _bool(value: Any, default: Optional[bool] = None) -> Optional[bool]:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on", "да"):
        return True
    if text in ("0", "false", "no", "n", "off", "нет"):
        return False
    return default

def _list(value: Any) -> List[str]:
    """«a, b» / ["a", "b"] → ["a", "b"] (labels, assignees, reviewers, topics)."""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = re.split(r"[,\n]", str(value))
    return [str(item).strip() for item in items if str(item).strip()]

def _number(args: Dict[str, Any], key: str = "number") -> int:
    raw = str(args.get(key) or args.get("issue") or args.get("pull") or "").strip().lstrip("#")
    if not raw.isdigit():
        raise _arg("Укажите %s — номер issue или pull request (число)" % key)
    return int(raw)

def _seg(value: Any) -> str:
    """One path segment from the model (branch names keep their slashes only where the API wants them)."""
    return quote(str(value).strip(), safe="")

def _path(value: Any) -> str:
    path = str(value or "").strip().strip("/")
    if not path or ".." in path.split("/"):
        raise _arg("Укажите путь файла внутри репозитория (без «..»)")
    return path

def _branch_name(value: Any) -> str:
    name = str(value or "").strip()
    if name.startswith("refs/heads/"):
        name = name[len("refs/heads/"):]
    if not name or name.startswith(("-", "/")) or ".." in name or re.search(r"[\s~^:?*\[\\]", name) or name.endswith((".lock", "/", ".")):
        raise _arg("Некорректное имя ветки: %s" % (name or "(пусто)"))
    return name

def _user(row: Any) -> str:
    return str((row or {}).get("login") or "") if isinstance(row, dict) else ""

def _short(text: Any, size: int = 300) -> str:
    body = " ".join(str(text or "").split())
    return body[:size]

def _default_branch(repo: str) -> str:
    return str(_gh("GET", "/repos/" + repo).get("default_branch") or "main")

def _head(repo: str, branch: str) -> str:
    row = _gh("GET", "/repos/%s/git/ref/heads/%s" % (repo, quote(branch, safe="/")))
    sha = str(((row or {}).get("object") or {}).get("sha") or "")
    if not sha:
        raise _arg("Ветка %s не найдена в %s" % (branch, repo))
    return sha

def _resolve_sha(repo: str, ref: str) -> str:
    """A branch, a tag or a commit sha → the commit sha."""
    ref = str(ref or "").strip()
    if re.fullmatch(r"[0-9a-f]{40}", ref):
        return ref
    row = _gh("GET", "/repos/%s/commits/%s" % (repo, quote(ref, safe="/")))
    sha = str((row or {}).get("sha") or "")
    if not sha:
        raise _arg("Не найдено: %s" % ref)
    return sha

def _commit_tree(repo: str, branch: str, message: str, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """One commit on ``branch`` with tree ``entries`` (create/update/delete) — the Git Data API."""
    parent = _head(repo, branch)
    base_tree = str((_gh("GET", "/repos/%s/git/commits/%s" % (repo, parent)).get("tree") or {}).get("sha") or "")
    tree = _gh("POST", "/repos/%s/git/trees" % repo, json_body={"base_tree": base_tree, "tree": entries})
    commit = _gh("POST", "/repos/%s/git/commits" % repo,
                 json_body={"message": message, "tree": tree.get("sha"), "parents": [parent]})
    _gh("PATCH", "/repos/%s/git/refs/heads/%s" % (repo, quote(branch, safe="/")),
        json_body={"sha": commit.get("sha"), "force": False})
    return {"commit": str(commit.get("sha") or "")[:10], "branch": branch,
            "url": commit.get("html_url") or "https://github.com/%s/commit/%s" % (repo, commit.get("sha"))}

def _tree_entries(repo: str, branch: str, path: str) -> Tuple[List[Dict[str, Any]], bool]:
    """Blobs at ``path`` (a file) or under it (a folder) on ``branch``."""
    parent = _head(repo, branch)
    tree_sha = str((_gh("GET", "/repos/%s/git/commits/%s" % (repo, parent)).get("tree") or {}).get("sha") or "")
    tree = _gh("GET", "/repos/%s/git/trees/%s" % (repo, tree_sha), params={"recursive": "1"})
    rows = [row for row in (tree.get("tree") or []) if isinstance(row, dict) and row.get("type") == "blob"
            and (row.get("path") == path or str(row.get("path") or "").startswith(path + "/"))]
    return rows, bool(tree.get("truncated"))

def _files_arg(value: Any) -> List[Dict[str, Any]]:
    """files: [{path, content}|{path, content_base64}] or {"path": "content"}."""
    if isinstance(value, dict):
        value = [{"path": key, "content": text} for key, text in value.items()]
    if isinstance(value, str):
        import json
        try:
            value = json.loads(value)
        except ValueError:
            raise _arg("files — список объектов {path, content}")
        return _files_arg(value)
    if not isinstance(value, list):
        return []
    out = []
    for row in value[:200]:
        if not isinstance(row, dict):
            continue
        out.append(row)
    return out

def _repo_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"full_name": row.get("full_name"), "private": row.get("private"), "visibility": row.get("visibility"),
            "description": row.get("description"), "default_branch": row.get("default_branch"),
            "url": row.get("html_url"), "stars": row.get("stargazers_count"), "fork": row.get("fork"),
            "archived": row.get("archived"), "updated_at": row.get("updated_at"), "language": row.get("language")}

def _issue_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"number": row.get("number"), "title": row.get("title"), "state": row.get("state"),
            "user": _user(row.get("user")), "labels": [lab.get("name") for lab in row.get("labels") or [] if isinstance(lab, dict)],
            "assignees": [_user(a) for a in row.get("assignees") or []], "comments": row.get("comments"),
            "pull_request": bool(row.get("pull_request")), "url": row.get("html_url"),
            "updated_at": row.get("updated_at"), "repo": "/".join(str(row.get("repository_url") or "").split("/")[-2:]) or None}

def _pull_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"number": row.get("number"), "title": row.get("title"), "state": row.get("state"),
            "draft": row.get("draft"), "merged": row.get("merged"), "mergeable": row.get("mergeable"),
            "user": _user(row.get("user")), "head": (row.get("head") or {}).get("ref"), "base": (row.get("base") or {}).get("ref"),
            "url": row.get("html_url"), "updated_at": row.get("updated_at")}

def _patch_files(files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    total = 0
    out = []
    for row in files[:100]:
        patch = str(row.get("patch") or "")
        cut = len(patch) > MAX_PATCH
        patch = patch[:MAX_PATCH]
        if total + len(patch) > MAX_PATCH_TOTAL:
            patch, cut = "", True
        total += len(patch)
        out.append({"file": row.get("filename"), "status": row.get("status"), "additions": row.get("additions"),
                    "deletions": row.get("deletions"), "patch": patch, **({"patch_truncated": True} if cut else {})})
    return out

def _gist_files(value: Any, allow_delete: bool) -> Dict[str, Any]:
    files: Dict[str, Any] = {}
    if isinstance(value, dict):
        for name, content in value.items():
            if content is None and allow_delete:
                files[str(name)] = None
            elif isinstance(content, dict):
                files[str(name)] = {k: v for k, v in content.items() if k in ("content", "filename")}
            else:
                files[str(name)] = {"content": str(content)}
    elif isinstance(value, list):
        for row in value:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or row.get("filename") or row.get("path") or "").strip()
            if not name:
                continue
            if row.get("delete") and allow_delete:
                files[name] = None
            else:
                files[name] = {"content": str(row.get("content") or "")}
                if row.get("new_name"):
                    files[name]["filename"] = str(row["new_name"])
    return files

def run(tool: str, args: Dict[str, Any]) -> Any:  # noqa: C901 - one dispatch table, kept flat on purpose
    limit = _int(args.get("limit"), 30, 1, 100)

    if tool == "github_create_repo":
        name = str(args.get("name") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name or "-"):
            raise _arg("Укажите name — имя нового репозитория (латиница, цифры, - _ .)")
        body: Dict[str, Any] = {"name": name, "description": str(args.get("description") or ""),
                                "private": bool(_bool(args.get("private"), True))}
        template = str(args.get("template") or "").strip()
        if template:
            owner = str(args.get("org") or "").strip() or None
            generate = {"name": name, "description": body["description"], "private": body["private"]}
            if owner:
                generate["owner"] = owner
            row = _gh("POST", "/repos/%s/generate" % _ig()._parse_repo(template), json_body=generate)
            return {"created": True, **_repo_row(row)}
        if _bool(args.get("auto_init"), True):
            body["auto_init"] = True
        if args.get("gitignore"):
            body["gitignore_template"] = str(args["gitignore"]).strip()
            body["auto_init"] = True
        if args.get("license"):
            body["license_template"] = str(args["license"]).strip().lower()
            body["auto_init"] = True
        org = str(args.get("org") or "").strip()
        row = _gh("POST", ("/orgs/%s/repos" % _seg(org)) if org else "/user/repos", json_body=body)
        return {"created": True, **_repo_row(row)}
    if tool == "github_update_repo":
        repo = _explicit_repo(args)
        body = {}
        if str(args.get("new_name") or "").strip():
            new = str(args["new_name"]).strip()
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", new):
                raise _arg("Некорректное новое имя репозитория: %s" % new)
            body["name"] = new
        for key in ("description", "homepage", "default_branch"):
            if args.get(key) is not None and str(args.get(key)) != "":
                body[key] = str(args[key])
        for key in ("private", "has_issues", "has_wiki", "archived"):
            value = _bool(args.get(key))
            if value is not None:
                body[key] = value
        topics = _list(args.get("topics")) if args.get("topics") is not None else None
        if not body and topics is None:
            raise _arg("Нечего менять: new_name, description, private, homepage, default_branch, has_issues, has_wiki, archived или topics")
        row = _gh("PATCH", "/repos/" + repo, json_body=body) if body else _gh("GET", "/repos/" + repo)
        out = {"updated": True, **_repo_row(row)}
        if topics is not None:
            name = row.get("full_name") or repo
            out["topics"] = (_gh("PUT", "/repos/%s/topics" % name, json_body={"names": [t.lower() for t in topics]}) or {}).get("names")
        return out
    if tool == "github_delete_repo":
        repo = _explicit_repo(args)
        _gh("DELETE", "/repos/" + repo)
        return {"deleted": True, "repo": repo}
    if tool == "github_fork_repo":
        repo = _explicit_repo(args)
        body = {}
        if args.get("org"):
            body["organization"] = str(args["org"]).strip()
        if args.get("name"):
            body["name"] = str(args["name"]).strip()
        if _bool(args.get("default_branch_only")) is not None:
            body["default_branch_only"] = bool(_bool(args.get("default_branch_only")))
        row = _gh("POST", "/repos/%s/forks" % repo, json_body=body)
        return {"forked": True, "note": "GitHub создаёт форк в фоне — файлы появятся через несколько секунд.", **_repo_row(row)}
    if tool == "github_search_repos":
        query = str(args.get("query") or "").strip()
        if not query:
            raise _arg("Укажите query (синтаксис поиска GitHub: language:python stars:>100 user:name)")
        rows = _gh("GET", "/search/repositories", params={"q": query, "per_page": limit})
        return {"total": rows.get("total_count"), "repos": [_repo_row(row) for row in rows.get("items") or []]}

    if tool == "github_list_branches":
        repo = _repo(args)
        rows = _gh("GET", "/repos/%s/branches" % repo, params={"per_page": limit})
        default = _default_branch(repo)
        return {"default_branch": default, "branches": [{"name": row.get("name"), "sha": str((row.get("commit") or {}).get("sha") or "")[:10],
                                                         "protected": row.get("protected")} for row in rows or []]}
    if tool == "github_create_branch":
        repo = _repo(args)
        branch = _branch_name(args.get("branch") or args.get("name"))
        source = str(args.get("from") or args.get("from_branch") or "").strip() or _default_branch(repo)
        sha = _resolve_sha(repo, source)
        _gh("POST", "/repos/%s/git/refs" % repo, json_body={"ref": "refs/heads/" + branch, "sha": sha})
        return {"created": True, "branch": branch, "from": source, "sha": sha[:10],
                "url": "https://github.com/%s/tree/%s" % (repo, quote(branch, safe="/"))}
    if tool == "github_delete_branch":
        repo = _repo(args)
        branch = _branch_name(args.get("branch") or args.get("name"))
        if branch == _default_branch(repo):
            raise _arg("Это ветка по умолчанию (%s) — её нельзя удалить, сначала смените default_branch" % branch)
        _gh("DELETE", "/repos/%s/git/refs/heads/%s" % (repo, quote(branch, safe="/")))
        return {"deleted": True, "branch": branch}
    if tool == "github_get_commit":
        repo = _repo(args)
        sha = str(args.get("sha") or args.get("ref") or "").strip()
        if not sha:
            raise _arg("Укажите sha коммита (или имя ветки)")
        row = _gh("GET", "/repos/%s/commits/%s" % (repo, quote(sha, safe="/")))
        commit = row.get("commit") or {}
        return {"sha": row.get("sha"), "message": commit.get("message"), "author": (commit.get("author") or {}).get("name"),
                "date": (commit.get("author") or {}).get("date"), "stats": row.get("stats"), "url": row.get("html_url"),
                "files": _patch_files(row.get("files") or [])}
    if tool == "github_compare":
        repo = _repo(args)
        base, head = str(args.get("base") or "").strip(), str(args.get("head") or "").strip()
        if not base or not head:
            raise _arg("Укажите base и head (ветки, теги или sha)")
        row = _gh("GET", "/repos/%s/compare/%s...%s" % (repo, quote(base, safe="/"), quote(head, safe="/")))
        return {"status": row.get("status"), "ahead_by": row.get("ahead_by"), "behind_by": row.get("behind_by"),
                "commits": [{"sha": str(c.get("sha") or "")[:10], "message": _short((c.get("commit") or {}).get("message"))}
                            for c in (row.get("commits") or [])[-50:]],
                "files": _patch_files(row.get("files") or []), "url": row.get("html_url")}
    if tool == "github_list_tags":
        rows = _gh("GET", "/repos/%s/tags" % _repo(args), params={"per_page": limit})
        return {"tags": [{"name": row.get("name"), "sha": str((row.get("commit") or {}).get("sha") or "")[:10]} for row in rows or []]}

    if tool == "github_delete_file":
        repo, path = _repo(args), _path(args.get("path"))
        branch = str(args.get("branch") or "").strip()
        params = {"ref": branch} if branch else None
        existing = _gh("GET", "/repos/%s/contents/%s" % (repo, quote(path, safe="/")), params=params)
        if isinstance(existing, list):

            branch = branch or _default_branch(repo)
            rows, _cut = _tree_entries(repo, branch, path)
            if not rows:
                raise _arg("Папка пуста или не найдена: %s" % path)
            entries = [{"path": row["path"], "mode": row.get("mode") or "100644", "type": "blob", "sha": None} for row in rows]
            result = _commit_tree(repo, branch, str(args.get("message") or "").strip() or "Delete %s" % path, entries)
            return {"deleted": True, "path": path, "files": len(rows), **result}
        body: Dict[str, Any] = {"message": str(args.get("message") or "").strip() or "Delete %s" % path, "sha": existing.get("sha")}
        if branch:
            body["branch"] = branch
        row = _gh("DELETE", "/repos/%s/contents/%s" % (repo, quote(path, safe="/")), json_body=body)
        return {"deleted": True, "path": path, "commit": str(((row or {}).get("commit") or {}).get("sha") or "")[:10]}
    if tool == "github_move_file":
        repo, path, new_path = _repo(args), _path(args.get("path")), _path(args.get("new_path") or args.get("to"))
        if path == new_path:
            raise _arg("Новый путь совпадает со старым")
        branch = str(args.get("branch") or "").strip() or _default_branch(repo)
        rows, _cut = _tree_entries(repo, branch, path)
        if not rows:
            raise _arg("Не найдено в ветке %s: %s" % (branch, path))
        entries: List[Dict[str, Any]] = []
        for row in rows:
            target = new_path + row["path"][len(path):]
            entries.append({"path": target, "mode": row.get("mode") or "100644", "type": "blob", "sha": row.get("sha")})
            entries.append({"path": row["path"], "mode": row.get("mode") or "100644", "type": "blob", "sha": None})
        result = _commit_tree(repo, branch, str(args.get("message") or "").strip() or "Move %s → %s" % (path, new_path), entries)
        return {"moved": True, "from": path, "to": new_path, "files": len(rows), **result}
    if tool == "github_commit_files":
        repo = _repo(args)
        files = _files_arg(args.get("files"))
        deletes = [_path(p) for p in _list(args.get("delete"))]
        if not files and not deletes:
            raise _arg("Укажите files: [{path, content}] и/или delete: [пути]")
        branch = str(args.get("branch") or "").strip() or _default_branch(repo)
        entries = []
        for row in files:
            path = _path(row.get("path"))
            if row.get("content_base64") is not None:
                blob = _gh("POST", "/repos/%s/git/blobs" % repo,
                           json_body={"content": str(row["content_base64"]), "encoding": "base64"})
                entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob.get("sha")})
            else:
                entries.append({"path": path, "mode": "100644", "type": "blob", "content": str(row.get("content") or "")})
        for path in deletes:
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
        message = str(args.get("message") or "").strip() or "MemuAI: update %d file(s)" % len(entries)
        result = _commit_tree(repo, branch, message, entries)
        return {"committed": True, "written": [e["path"] for e in entries if e.get("sha", "") is not None],
                "deleted": deletes, **result}

    if tool == "github_list_pulls":
        state = str(args.get("state") or "open").lower()
        params: Dict[str, Any] = {"state": state if state in ("open", "closed", "all") else "open", "per_page": limit}
        for key in ("base", "head"):
            if args.get(key):
                params[key] = str(args[key])
        rows = _gh("GET", "/repos/%s/pulls" % _repo(args), params=params)
        return {"pulls": [_pull_row(row) for row in rows or []]}
    if tool == "github_get_pull":
        repo, number = _repo(args), _number(args)
        row = _gh("GET", "/repos/%s/pulls/%d" % (repo, number))
        files = _gh("GET", "/repos/%s/pulls/%d/files" % (repo, number), params={"per_page": 100})
        return {**_pull_row(row), "body": str(row.get("body") or "")[:20000], "additions": row.get("additions"),
                "deletions": row.get("deletions"), "changed_files": row.get("changed_files"),
                "mergeable_state": row.get("mergeable_state"), "files": _patch_files(files if isinstance(files, list) else [])}
    if tool == "github_create_pull":
        repo = _repo(args)
        title, head = str(args.get("title") or "").strip(), str(args.get("head") or "").strip()
        if not title or not head:
            raise _arg("Укажите title и head (ветка с изменениями; из форка — owner:branch)")
        body = {"title": title, "head": head, "base": str(args.get("base") or "").strip() or _default_branch(repo),
                "body": str(args.get("body") or ""), "draft": bool(_bool(args.get("draft"), False))}
        row = _gh("POST", "/repos/%s/pulls" % repo, json_body=body)
        return {"created": True, **_pull_row(row)}
    if tool == "github_update_pull":
        repo, number = _repo(args), _number(args)
        body = {key: str(args[key]) for key in ("title", "body", "base") if args.get(key) not in (None, "")}
        state = str(args.get("state") or "").lower()
        if state in ("open", "closed"):
            body["state"] = state
        if not body:
            raise _arg("Нечего менять: title, body, base или state (open/closed)")
        row = _gh("PATCH", "/repos/%s/pulls/%d" % (repo, number), json_body=body)
        return {"updated": True, **_pull_row(row)}
    if tool == "github_merge_pull":
        repo, number = _repo(args), _number(args)
        method = str(args.get("method") or "merge").lower()
        if method not in ("merge", "squash", "rebase"):
            raise _arg("method: merge, squash или rebase")
        body = {"merge_method": method}
        for key in ("commit_title", "commit_message"):
            if args.get(key):
                body[key] = str(args[key])
        row = _gh("PUT", "/repos/%s/pulls/%d/merge" % (repo, number), json_body=body)
        return {"merged": bool(row.get("merged")), "sha": str(row.get("sha") or "")[:10], "message": row.get("message")}
    if tool == "github_review_pull":
        repo, number = _repo(args), _number(args)
        event = str(args.get("event") or "COMMENT").upper().replace(" ", "_")
        event = {"APPROVED": "APPROVE", "CHANGES": "REQUEST_CHANGES", "REQUEST": "REQUEST_CHANGES"}.get(event, event)
        if event not in ("APPROVE", "REQUEST_CHANGES", "COMMENT"):
            raise _arg("event: APPROVE, REQUEST_CHANGES или COMMENT")
        body = {"event": event, "body": str(args.get("body") or "")}
        if event != "APPROVE" and not body["body"].strip():
            raise _arg("Для REQUEST_CHANGES и COMMENT нужен body — текст отзыва")
        row = _gh("POST", "/repos/%s/pulls/%d/reviews" % (repo, number), json_body=body)
        return {"review_id": row.get("id"), "state": row.get("state"), "url": row.get("html_url")}
    if tool == "github_request_reviewers":
        repo, number = _repo(args), _number(args)
        reviewers = _list(args.get("reviewers"))
        if not reviewers:
            raise _arg("Укажите reviewers — логины GitHub")
        row = _gh("POST", "/repos/%s/pulls/%d/requested_reviewers" % (repo, number), json_body={"reviewers": reviewers})
        return {"requested": [_user(u) for u in row.get("requested_reviewers") or []]}

    if tool == "github_get_issue":
        repo, number = _repo(args), _number(args)
        row = _gh("GET", "/repos/%s/issues/%d" % (repo, number))
        comments = _gh("GET", "/repos/%s/issues/%d/comments" % (repo, number), params={"per_page": 30})
        return {**_issue_row(row), "body": str(row.get("body") or "")[:20000],
                "comments_list": [{"id": c.get("id"), "user": _user(c.get("user")), "body": str(c.get("body") or "")[:4000],
                                   "created_at": c.get("created_at")} for c in (comments if isinstance(comments, list) else [])]}
    if tool == "github_update_issue":
        repo, number = _repo(args), _number(args)
        body = {key: str(args[key]) for key in ("title", "body") if args.get(key) not in (None, "")}
        state = str(args.get("state") or "").lower()
        if state in ("open", "closed"):
            body["state"] = state
            reason = str(args.get("state_reason") or "").lower()
            if reason in ("completed", "not_planned", "reopened", "duplicate"):
                body["state_reason"] = reason
        if args.get("labels") is not None:
            body["labels"] = _list(args.get("labels"))
        if args.get("assignees") is not None:
            body["assignees"] = _list(args.get("assignees"))
        if args.get("milestone") not in (None, ""):
            body["milestone"] = None if str(args["milestone"]).lower() in ("none", "null") else _int(args["milestone"], 0, 0, 1 << 30)
        if not body:
            raise _arg("Нечего менять: title, body, state (open/closed), labels, assignees, milestone")
        row = _gh("PATCH", "/repos/%s/issues/%d" % (repo, number), json_body=body)
        return {"updated": True, **_issue_row(row)}
    if tool == "github_comment":
        repo, number = _repo(args), _number(args)
        text = str(args.get("body") or "").strip()
        if not text:
            raise _arg("Укажите body — текст комментария")
        row = _gh("POST", "/repos/%s/issues/%d/comments" % (repo, number), json_body={"body": text})
        return {"comment_id": row.get("id"), "url": row.get("html_url")}
    if tool == "github_list_comments":
        repo, number = _repo(args), _number(args)
        rows = _gh("GET", "/repos/%s/issues/%d/comments" % (repo, number), params={"per_page": limit})
        return {"comments": [{"id": c.get("id"), "user": _user(c.get("user")), "body": str(c.get("body") or "")[:4000],
                              "created_at": c.get("created_at")} for c in rows or []]}
    if tool == "github_list_labels":
        rows = _gh("GET", "/repos/%s/labels" % _repo(args), params={"per_page": 100})
        return {"labels": [{"name": row.get("name"), "color": row.get("color"), "description": row.get("description")} for row in rows or []]}
    if tool == "github_create_label":
        repo = _repo(args)
        name = str(args.get("name") or "").strip()
        if not name:
            raise _arg("Укажите name метки")
        color = str(args.get("color") or "ededed").strip().lstrip("#")
        if not re.fullmatch(r"[0-9a-fA-F]{6}", color):
            color = "ededed"
        row = _gh("POST", "/repos/%s/labels" % repo, json_body={"name": name, "color": color.lower(),
                                                                 "description": str(args.get("description") or "")[:100]})
        return {"created": True, "name": row.get("name"), "color": row.get("color")}
    if tool == "github_add_labels":
        repo, number = _repo(args), _number(args)
        labels = _list(args.get("labels"))
        if not labels:
            raise _arg("Укажите labels")
        rows = _gh("POST", "/repos/%s/issues/%d/labels" % (repo, number), json_body={"labels": labels})
        return {"labels": [row.get("name") for row in rows or [] if isinstance(row, dict)]}
    if tool == "github_remove_label":
        repo, number = _repo(args), _number(args)
        label = str(args.get("label") or "").strip()
        if not label:
            raise _arg("Укажите label")
        rows = _gh("DELETE", "/repos/%s/issues/%d/labels/%s" % (repo, number, _seg(label)))
        return {"removed": label, "labels": [row.get("name") for row in rows or [] if isinstance(row, dict)]}
    if tool == "github_add_assignees":
        repo, number = _repo(args), _number(args)
        people = _list(args.get("assignees"))
        if not people:
            raise _arg("Укажите assignees — логины GitHub")
        row = _gh("POST", "/repos/%s/issues/%d/assignees" % (repo, number), json_body={"assignees": people})
        return {"assignees": [_user(a) for a in row.get("assignees") or []]}
    if tool == "github_search_issues":
        query = str(args.get("query") or "").strip()
        if not query:
            raise _arg("Укажите query (например: repo:owner/name is:open label:bug, is:pr author:@me)")
        rows = _gh("GET", "/search/issues", params={"q": query, "per_page": limit, "sort": "updated"})
        return {"total": rows.get("total_count"), "items": [_issue_row(row) for row in rows.get("items") or []]}

    if tool == "github_list_releases":
        rows = _gh("GET", "/repos/%s/releases" % _repo(args), params={"per_page": limit})
        return {"releases": [{"id": row.get("id"), "tag": row.get("tag_name"), "name": row.get("name"), "draft": row.get("draft"),
                              "prerelease": row.get("prerelease"), "published_at": row.get("published_at"), "url": row.get("html_url"),
                              "assets": [a.get("name") for a in row.get("assets") or []]} for row in rows or []]}
    if tool == "github_create_release":
        repo = _repo(args)
        tag = str(args.get("tag") or args.get("tag_name") or "").strip()
        if not tag:
            raise _arg("Укажите tag, например v1.2.0")
        body = {"tag_name": tag, "name": str(args.get("name") or tag), "body": str(args.get("body") or ""),
                "draft": bool(_bool(args.get("draft"), False)), "prerelease": bool(_bool(args.get("prerelease"), False)),
                "generate_release_notes": bool(_bool(args.get("generate_notes"), not str(args.get("body") or "").strip()))}
        if args.get("target"):
            body["target_commitish"] = str(args["target"])
        row = _gh("POST", "/repos/%s/releases" % repo, json_body=body)
        return {"created": True, "id": row.get("id"), "tag": row.get("tag_name"), "url": row.get("html_url"), "draft": row.get("draft")}
    if tool == "github_delete_release":
        repo = _repo(args)
        release_id = str(args.get("release_id") or "").strip()
        tag = str(args.get("tag") or "").strip()
        if not release_id:
            if not tag:
                raise _arg("Укажите release_id или tag")
            release_id = str(_gh("GET", "/repos/%s/releases/tags/%s" % (repo, _seg(tag))).get("id") or "")
        _gh("DELETE", "/repos/%s/releases/%s" % (repo, _seg(release_id)))
        out = {"deleted": True, "release_id": release_id}
        if tag and _bool(args.get("delete_tag"), False):
            _gh("DELETE", "/repos/%s/git/refs/tags/%s" % (repo, quote(tag, safe="/")))
            out["tag_deleted"] = tag
        return out

    if tool == "github_list_gists":
        rows = _gh("GET", "/gists", params={"per_page": limit})
        return {"gists": [{"id": row.get("id"), "description": row.get("description"), "public": row.get("public"),
                           "files": list((row.get("files") or {}).keys()), "url": row.get("html_url"),
                           "updated_at": row.get("updated_at")} for row in rows or []]}
    if tool == "github_read_gist":
        gist = str(args.get("gist_id") or args.get("id") or "").strip()
        if not gist:
            raise _arg("Укажите gist_id")
        row = _gh("GET", "/gists/" + _seg(gist))
        return {"id": row.get("id"), "description": row.get("description"), "public": row.get("public"), "url": row.get("html_url"),
                "files": [{"name": name, "language": f.get("language"), "content": str(f.get("content") or "")[:40000],
                           "truncated": bool(f.get("truncated"))} for name, f in (row.get("files") or {}).items()]}
    if tool == "github_create_gist":
        files = _gist_files(args.get("files"), allow_delete=False)
        if not files:
            raise _arg("Укажите files — {\"имя.txt\": \"текст\"} или [{name, content}]")
        row = _gh("POST", "/gists", json_body={"description": str(args.get("description") or ""),
                                               "public": bool(_bool(args.get("public"), False)), "files": files})
        return {"created": True, "id": row.get("id"), "url": row.get("html_url"), "public": row.get("public")}
    if tool == "github_update_gist":
        gist = str(args.get("gist_id") or args.get("id") or "").strip()
        if not gist:
            raise _arg("Укажите gist_id")
        body = {}
        files = _gist_files(args.get("files"), allow_delete=True)
        if files:
            body["files"] = files
        if args.get("description") is not None:
            body["description"] = str(args["description"])
        if not body:
            raise _arg("Нечего менять: files или description")
        row = _gh("PATCH", "/gists/" + _seg(gist), json_body=body)
        return {"updated": True, "id": row.get("id"), "files": list((row.get("files") or {}).keys()), "url": row.get("html_url")}
    if tool == "github_delete_gist":
        gist = str(args.get("gist_id") or args.get("id") or "").strip()
        if not gist:
            raise _arg("Укажите gist_id")
        _gh("DELETE", "/gists/" + _seg(gist))
        return {"deleted": True, "gist_id": gist}

    if tool == "github_list_workflows":
        rows = _gh("GET", "/repos/%s/actions/workflows" % _repo(args), params={"per_page": 100})
        return {"workflows": [{"id": row.get("id"), "name": row.get("name"), "path": row.get("path"), "state": row.get("state")}
                              for row in rows.get("workflows") or []]}
    if tool == "github_list_runs":
        repo = _repo(args)
        params = {"per_page": limit}
        for key in ("branch", "status"):
            if args.get(key):
                params[key] = str(args[key])
        workflow = str(args.get("workflow") or "").strip()
        path = "/repos/%s/actions/workflows/%s/runs" % (repo, _seg(workflow)) if workflow else "/repos/%s/actions/runs" % repo
        rows = _gh("GET", path, params=params)
        return {"total": rows.get("total_count"), "runs": [
            {"id": row.get("id"), "name": row.get("name"), "status": row.get("status"), "conclusion": row.get("conclusion"),
             "branch": row.get("head_branch"), "event": row.get("event"), "sha": str(row.get("head_sha") or "")[:10],
             "created_at": row.get("created_at"), "url": row.get("html_url")} for row in rows.get("workflow_runs") or []]}
    if tool == "github_get_run":
        repo = _repo(args)
        run_id = _int(args.get("run_id"), 0, 0, 1 << 62)
        if not run_id:
            raise _arg("Укажите run_id (его даёт github_list_runs)")
        row = _gh("GET", "/repos/%s/actions/runs/%d" % (repo, run_id))
        jobs = _gh("GET", "/repos/%s/actions/runs/%d/jobs" % (repo, run_id), params={"per_page": 100})
        return {"id": row.get("id"), "name": row.get("name"), "status": row.get("status"), "conclusion": row.get("conclusion"),
                "branch": row.get("head_branch"), "url": row.get("html_url"), "jobs": [
                    {"id": j.get("id"), "name": j.get("name"), "status": j.get("status"), "conclusion": j.get("conclusion"),
                     "failed_steps": [s.get("name") for s in j.get("steps") or [] if s.get("conclusion") == "failure"]}
                    for j in jobs.get("jobs") or []]}
    if tool == "github_job_logs":
        repo = _repo(args)
        job_id = _int(args.get("job_id"), 0, 0, 1 << 62)
        if not job_id:
            raise _arg("Укажите job_id (его даёт github_get_run)")
        ig = _ig()
        token = ig._credentials("github")["token"]
        data, _cut, _type = ig._stream_text("GitHub", ig.GITHUB_API + "/repos/%s/actions/jobs/%d/logs" % (repo, job_id),
                                            ig._github_headers(token), on_error=ig._raise_github)
        text = data.decode("utf-8", "replace")
        return {"job_id": job_id, "log": text[-MAX_LOG:], "truncated": len(text) > MAX_LOG,
                "note": "Показан конец журнала." if len(text) > MAX_LOG else ""}
    if tool == "github_rerun":
        repo = _repo(args)
        run_id = _int(args.get("run_id"), 0, 0, 1 << 62)
        if not run_id:
            raise _arg("Укажите run_id")
        failed = bool(_bool(args.get("failed_only"), False))
        _gh("POST", "/repos/%s/actions/runs/%d/%s" % (repo, run_id, "rerun-failed-jobs" if failed else "rerun"))
        return {"rerun": True, "run_id": run_id, "failed_only": failed}
    if tool == "github_cancel_run":
        repo = _repo(args)
        run_id = _int(args.get("run_id"), 0, 0, 1 << 62)
        if not run_id:
            raise _arg("Укажите run_id")
        _gh("POST", "/repos/%s/actions/runs/%d/cancel" % (repo, run_id))
        return {"cancelled": True, "run_id": run_id}
    if tool == "github_dispatch_workflow":
        repo = _repo(args)
        workflow = str(args.get("workflow") or "").strip()
        if not workflow:
            raise _arg("Укажите workflow — имя файла (ci.yml) или id; у workflow должен быть триггер workflow_dispatch")
        inputs = args.get("inputs") if isinstance(args.get("inputs"), dict) else {}
        ref = str(args.get("ref") or "").strip() or _default_branch(repo)
        _gh("POST", "/repos/%s/actions/workflows/%s/dispatches" % (repo, _seg(workflow)),
            json_body={"ref": ref, "inputs": {str(k): str(v) for k, v in inputs.items()}})
        return {"dispatched": True, "workflow": workflow, "ref": ref,
                "note": "Запуск появится в github_list_runs через несколько секунд."}

    if tool == "github_list_collaborators":
        rows = _gh("GET", "/repos/%s/collaborators" % _repo(args), params={"per_page": 100})
        return {"collaborators": [{"login": row.get("login"), "role": row.get("role_name"),
                                   "admin": (row.get("permissions") or {}).get("admin")} for row in rows or []]}
    if tool == "github_add_collaborator":
        repo = _explicit_repo(args)
        user = str(args.get("username") or args.get("user") or "").strip().lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", user or "-"):
            raise _arg("Укажите username — логин GitHub")
        permission = str(args.get("permission") or "push").lower()
        if permission not in ("pull", "triage", "push", "maintain", "admin"):
            permission = "push"
        row = _gh("PUT", "/repos/%s/collaborators/%s" % (repo, user), json_body={"permission": permission})
        return {"invited": bool(row), "already_collaborator": not row, "username": user, "permission": permission,
                "url": (row or {}).get("html_url")}
    if tool == "github_remove_collaborator":
        repo = _explicit_repo(args)
        user = str(args.get("username") or args.get("user") or "").strip().lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", user or "-"):
            raise _arg("Укажите username — логин GitHub")
        _gh("DELETE", "/repos/%s/collaborators/%s" % (repo, user))
        return {"removed": True, "username": user}

    raise _arg("Неизвестный инструмент GitHub: %s" % tool)

def token_info() -> Dict[str, Any]:
    """Who the token is and what it may do: classic scopes (X-OAuth-Scopes) or «fine-grained» (no header)."""
    ig = _ig()
    token = ig._credentials("github")["token"]
    response = ig._send("GitHub", "GET", ig.GITHUB_API + "/user", headers=ig._github_headers(token))
    if response.status_code >= 400:
        raise ig._github_error(response)
    user = response.json() if response.content else {}
    header = response.headers.get("x-oauth-scopes")
    kind = "classic" if header is not None or token.startswith(("ghp_", "gho_")) else ("fine-grained" if token.startswith("github_pat_") else "unknown")
    scopes = sorted({s.strip() for s in (header or "").split(",") if s.strip()})
    missing = [s for s in ("repo", "workflow", "gist", "delete_repo") if kind == "classic" and s not in scopes]
    return {"login": user.get("login"), "name": user.get("name"), "kind": kind, "scopes": scopes, "missing": missing,
            "public_repos": user.get("public_repos"), "private_repos": user.get("total_private_repos")}
