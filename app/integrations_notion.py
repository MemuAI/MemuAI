"""Round 30: Notion — sign-in and the assistant's tools (search, read pages and databases, create / append).

Two ways to connect (Settings → Integrations → Notion):

* **Integration token** (the simple path): in the Notion Developer portal
  (app.notion.com/developers, or Notion Settings → Connections → Go to developer
  portal) the user opens Internal connections → Create a new connection, copies
  the API token from its Configuration tab (``ntn_…``; ``secret_…`` for old
  integrations) and shares pages with it (the connection's Content access tab,
  or on a page «•••» → Connections → + Add connection).
* **Sign in with Notion** (OAuth of a *public* integration): MemuAI opens
  Notion's consent page in the default browser and receives the code on a
  loopback listener ``http://127.0.0.1:47652/notion/callback`` (Notion needs the
  redirect URI registered exactly, so the port is fixed).  The client comes from
  the user (their own public integration) or from the build
  (``MEMUAI_NOTION_CLIENT_ID`` / ``_SECRET`` → :mod:`app.oauth_config`).  Access
  tokens are refreshed with the refresh token; «Disconnect» revokes the token.

Tokens and the client secret live in the protected store of the Google account
(:class:`app.gdrive_sync_crypto.SecretStore` — DPAPI on Windows); the
non-secret part (mode, workspace name, client id) in ``integrations.json``
under ``notion``.  HTTP goes through :func:`app.integrations._send` (tests swap
``integrations._client`` for an ``httpx.MockTransport``).
"""
from __future__ import annotations

import base64
import html
import re
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
REDIRECT_PORT = 47652
REDIRECT_PATH = "/notion/callback"
REDIRECT_URI = "http://127.0.0.1:%d%s" % (REDIRECT_PORT, REDIRECT_PATH)
FLOW_TIMEOUT = 300.0
MAX_TEXT = 100000
MAX_BLOCKS = 600
_ID = re.compile(r"([0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12})")

_flow: Dict[str, Any] = {"state": "idle"}
_server: Dict[str, Optional[HTTPServer]] = {"server": None}
_access: Dict[str, Any] = {"token": "", "expires": 0.0}
_lock = threading.Lock()

open_browser: Optional[Callable[[str], Any]] = None

def _ig():
    from . import integrations
    return integrations

def _secrets():
    from . import google_account
    return google_account.sync_service().store

def _err(text: str, argument: bool = False):
    ig = _ig()
    return (ig.IntegrationArgumentError if argument else ig.IntegrationError)(text)

def _meta() -> Dict[str, Any]:
    data = _ig()._load_store().get("notion") or {}
    return data if isinstance(data, dict) else {}

def _save_meta(**values: Any) -> Dict[str, Any]:
    ig = _ig()
    with ig._store_transaction():
        store = ig._load_store()
        current = dict(store.get("notion") or {})
        for key, value in values.items():
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
        store["notion"] = current
        ig._save_store(store)
    return current

def client() -> Tuple[str, str, str]:
    """``(client_id, client_secret, source)``: the user's public integration wins, then the build/env one."""
    cid = str(_meta().get("client_id") or "").strip()
    if cid:
        return cid, _secrets().get("notion_client_secret"), "user"
    from .oauth_config import client as configured
    cid, secret, source = configured("notion")
    return (cid, secret, source) if cid else ("", "", "")

def connected() -> bool:
    return bool(_secrets().get("notion_token"))

def status() -> Dict[str, Any]:
    meta = _meta()
    cid, _secret, source = client()
    flow = dict(_flow)
    return {"connected": connected(), "mode": str(meta.get("mode") or ""), "account": str(meta.get("account") or ""),
            "workspace_icon": str(meta.get("workspace_icon") or "") if str(meta.get("workspace_icon") or "").startswith("https://") else "",
            "checked": meta.get("checked") or 0, "last_error": str(meta.get("last_error") or ""),
            "oauth": {"available": bool(cid), "source": source, "own_client_id": str(meta.get("client_id") or ""),
                      "own_has_secret": bool(_secrets().get("notion_client_secret")), "redirect_uri": REDIRECT_URI},
            "flow": {key: flow[key] for key in ("state", "error_ru", "error_en", "auth_url") if key in flow}}

def save_token(token: str) -> Dict[str, Any]:
    """The simple path: an internal integration secret pasted by the user — checked at once (/users/me)."""
    value = re.sub(r"\s+", "", str(token or "").strip().strip('"').strip("'"))
    value = re.sub(r"^(?:bearer)", "", value, flags=re.I)
    if not value:
        raise _err("Вставьте токен интеграции Notion (начинается с ntn_ или secret_)", argument=True)
    if not re.match(r"^(ntn_|secret_)[A-Za-z0-9]{20,}$", value):
        raise _err("Это не похоже на токен интеграции Notion: он начинается с ntn_ (или secret_ у старых)", argument=True)
    me = _request("GET", "/users/me", token=value)
    bot = me.get("bot") or {}
    account = str(bot.get("workspace_name") or me.get("name") or "Notion")
    store = _secrets()
    store.set("notion_token", value)
    store.delete("notion_refresh_token")
    _access.update(token="", expires=0.0)
    _save_meta(mode="token", account=account[:200], checked=int(time.time()), last_error=None, workspace_icon=None)
    return status()

def save_client(client_id: str, client_secret: str) -> Dict[str, Any]:
    cid = str(client_id or "").strip()
    secret = str(client_secret or "").strip()
    if cid and not re.match(r"^[0-9a-fA-F-]{20,}$", cid):
        raise _err("OAuth client ID Notion — это UUID из вкладки Configuration публичной интеграции", argument=True)
    _save_meta(client_id=cid or None)
    if secret:
        _secrets().set("notion_client_secret", secret)
    elif not cid:
        _secrets().delete("notion_client_secret")
    return status()

def disconnect() -> Dict[str, Any]:
    store = _secrets()
    token = store.get("notion_token")
    meta = _meta()
    if token and meta.get("mode") == "oauth":
        cid, secret, _source = client()
        if cid:
            try:
                _ig()._send("Notion", "POST", API + "/oauth/revoke", json={"token": token},
                            headers=_basic(cid, secret))
            except Exception:
                pass
    store.delete("notion_token")
    store.delete("notion_refresh_token")
    _access.update(token="", expires=0.0)
    _save_meta(mode=None, account=None, checked=None, last_error=None, workspace_icon=None, workspace_id=None, bot_id=None)
    return status()

def _basic(cid: str, secret: str) -> Dict[str, str]:
    raw = base64.b64encode(("%s:%s" % (cid, secret)).encode("utf-8")).decode("ascii")
    return {"Authorization": "Basic " + raw, "Notion-Version": VERSION, "Content-Type": "application/json"}

_DONE = """<!doctype html><html><head><meta charset="utf-8"><title>MemuAI · Notion</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;background:#1a1a1c;color:#eee;display:grid;place-items:center;height:100vh;margin:0}}
.card{{max-width:440px;padding:28px 32px;border-radius:16px;background:#26262a}}h1{{font-size:20px;margin:0 0 8px}}
p{{margin:6px 0;color:#bbb}}.ok{{color:#7fd2be}}.bad{{color:#f4a09a}}</style></head><body><div class="card">
<h1 class="{cls}">{ru}</h1><p>{text_ru}</p><h1 class="{cls}" style="font-size:17px">{en}</h1><p>{text_en}</p></div></body></html>"""

def start_oauth() -> Dict[str, Any]:
    cid, secret, source = client()
    if not cid or not secret:
        raise _err("Для входа через Notion нужен OAuth-клиент публичной интеграции — или вставьте токен внутренней интеграции", argument=True)
    cancel_oauth()
    state = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            parts = urllib.parse.urlsplit(self.path)
            if parts.path != REDIRECT_PATH:
                self.send_response(404)
                self.end_headers()
                return
            ok, ru, en, text_ru, text_en = _finish(urllib.parse.parse_qs(parts.query), state)
            page = _DONE.format(cls="ok" if ok else "bad", ru=html.escape(ru), en=html.escape(en),
                                text_ru=html.escape(text_ru), text_en=html.escape(text_en)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(page)

    try:
        server = HTTPServer(("127.0.0.1", REDIRECT_PORT), Handler)
    except OSError as exc:
        raise _err("Порт %d занят другой программой — закройте её и попробуйте снова (%s)" % (REDIRECT_PORT, type(exc).__name__)) from exc
    server.timeout = 1.0
    _server["server"] = server
    url = API + "/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": cid, "response_type": "code", "owner": "user", "redirect_uri": REDIRECT_URI, "state": state})
    _flow.clear()
    _flow.update({"state": "waiting", "auth_url": url, "started": time.time(), "source": source})

    def serve() -> None:
        deadline = time.time() + FLOW_TIMEOUT
        while _server["server"] is server and time.time() < deadline and _flow.get("state") == "waiting":
            try:
                server.handle_request()
            except Exception:
                break
        if _flow.get("state") == "waiting" and _server["server"] is server:
            _flow.clear()
            _flow.update({"state": "error", "error_ru": "Время ожидания входа истекло", "error_en": "Sign-in timed out"})
        _close(server)

    threading.Thread(target=serve, name="memuai-notion-oauth", daemon=True).start()
    opener = open_browser
    if opener is None:
        import webbrowser
        opener = webbrowser.open
    try:
        opener(url)
    except Exception:
        pass
    return {"state": "waiting", "auth_url": url}

def _close(server: Optional[HTTPServer] = None) -> None:
    target = server or _server["server"]
    if target is None:
        return
    if _server["server"] is target:
        _server["server"] = None
    try:
        target.server_close()
    except Exception:
        pass

def cancel_oauth() -> None:
    if _flow.get("state") == "waiting":
        _flow.clear()
        _flow["state"] = "idle"
    _close()

def _finish(query: Dict[str, List[str]], state: str):
    if not secrets.compare_digest((query.get("state") or [""])[0], state):
        _flow.clear()
        _flow.update({"state": "error", "error_ru": "Ответ Notion не совпал с запросом (state)",
                      "error_en": "Notion's answer did not match the request (state)"})
        return False, "Вход не выполнен", "Sign-in failed", "Попробуйте ещё раз из MemuAI.", "Try again from MemuAI."
    error = (query.get("error") or [""])[0]
    if error:
        denied = error == "access_denied"
        _flow.clear()
        _flow.update({"state": "error", "error_ru": "Доступ не выдан" if denied else "Notion вернул ошибку: %s" % error[:60],
                      "error_en": "Access was not granted" if denied else "Notion returned an error: %s" % error[:60]})
        return False, "Доступ не выдан", "Access not granted", "Окно можно закрыть.", "You can close this tab."
    try:
        exchange((query.get("code") or [""])[0])
    except Exception as exc:
        _flow.clear()
        _flow.update({"state": "error", "error_ru": str(exc)[:300], "error_en": str(exc)[:300]})
        return False, "Вход не выполнен", "Sign-in failed", str(exc)[:200], str(exc)[:200]
    _flow.clear()
    _flow["state"] = "done"
    return True, "Notion подключён", "Notion connected", "Вернитесь в MemuAI — окно можно закрыть.", "Return to MemuAI — you can close this tab."

def _token_call(body: Dict[str, Any]) -> Dict[str, Any]:
    cid, secret, _source = client()
    response = _ig()._send("Notion", "POST", API + "/oauth/token", json=body, headers=_basic(cid, secret))
    data = _ig()._error_payload(response)
    if response.status_code >= 400:
        code = str(data.get("error") or data.get("code") or response.status_code)
        if code == "invalid_grant":
            raise _err("Notion отозвал доступ (invalid_grant) — войдите заново: Настройки → Интеграции → Notion.")
        if code in ("invalid_client", "unauthorized_client"):
            raise _err("Notion не принял OAuth client ID / secret (%s) — проверьте их в Настройки → Интеграции → Notion." % code)
        raise _err("Notion не выдал токен (%s)" % code)
    if not data.get("access_token"):
        raise _err("Notion не вернул access_token")
    return data

def exchange(code: str) -> None:
    data = _token_call({"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI})
    store = _secrets()
    store.set("notion_token", str(data["access_token"]))
    if data.get("refresh_token"):
        store.set("notion_refresh_token", str(data["refresh_token"]))
    else:
        store.delete("notion_refresh_token")
    _access.update(token=str(data["access_token"]), expires=time.time() + float(data.get("expires_in") or 3000) - 60)
    _save_meta(mode="oauth", account=str(data.get("workspace_name") or "Notion")[:200],
               workspace_icon=str(data.get("workspace_icon") or "")[:500] or None,
               workspace_id=str(data.get("workspace_id") or "") or None, bot_id=str(data.get("bot_id") or "") or None,
               checked=int(time.time()), last_error=None)

def _refresh() -> str:
    store = _secrets()
    refresh = store.get("notion_refresh_token")
    if not refresh:
        return ""
    with _lock:
        data = _token_call({"grant_type": "refresh_token", "refresh_token": refresh})
        store.set("notion_token", str(data["access_token"]))
        if data.get("refresh_token"):
            store.set("notion_refresh_token", str(data["refresh_token"]))
        _access.update(token=str(data["access_token"]), expires=time.time() + float(data.get("expires_in") or 3000) - 60)
    return str(data["access_token"])

def _error(response) -> Exception:
    data = _ig()._error_payload(response)
    code = str(data.get("code") or "")
    message = str(data.get("message") or "")[:300]
    status = response.status_code
    if status == 401 or code == "unauthorized":
        return _err("Notion не принял токен (401). Подключите Notion заново: Настройки → Интеграции → Notion.")
    if status == 404 or code in ("object_not_found", "restricted_resource"):
        return _err("Notion: страница или база не найдена, либо к ней не дан доступ интеграции. Откройте её в Notion → «•••» → "
                    "Connections → добавьте вашу интеграцию (%s)." % (message or code or status), argument=True)
    if status == 403:
        return _err("Notion запретил действие (403: %s). У интеграции нет нужных прав (Capabilities: Read/Update/Insert content)."
                    % (message or code))
    if status == 429 or code == "rate_limited":
        return _err("Notion: слишком много запросов. Подождите немного и повторите.")
    if status == 400 or code == "validation_error":
        return _err("Notion не принял запрос: %s" % (message or code), argument=True)
    if status >= 500:
        return _err("Notion временно недоступен (%s). Попробуйте позже." % status)
    return _err("Notion %s: %s" % (status, message or code))

def _request(method: str, path: str, *, json_body: Optional[Dict[str, Any]] = None,
             params: Optional[Dict[str, Any]] = None, token: str = "") -> Dict[str, Any]:
    ig = _ig()
    fixed = bool(token)
    for attempt in range(2):
        if not fixed:
            token = _secrets().get("notion_token")
            if not token:
                raise _err("Notion не подключён. Откройте Настройки → Интеграции → Notion.")
        headers = {"Authorization": "Bearer " + token, "Notion-Version": VERSION}
        response = ig._send("Notion", method, API + path, headers=headers, json=json_body, params=params)
        if response.status_code == 401 and attempt == 0 and not fixed and _secrets().get("notion_refresh_token"):
            _refresh()
            continue
        break
    if response.status_code >= 400:
        raise _error(response)
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}

def test() -> Dict[str, Any]:
    me = _request("GET", "/users/me")
    bot = me.get("bot") or {}
    account = str(bot.get("workspace_name") or me.get("name") or "Notion")
    _save_meta(account=account[:200], checked=int(time.time()), last_error=None)
    return {"account": account}

def _id(value: Any, what: str = "page_id") -> str:
    """An id from what people paste: a bare id, a dashed UUID or a notion.so URL (the last 32 hex chars)."""
    text = str(value or "").strip()
    found = _ID.findall(text.split("?")[0]) or _ID.findall(text)
    if not found:
        raise _err("Укажите %s — id или ссылку на страницу Notion" % what, argument=True)
    raw = found[-1].replace("-", "").lower()
    return "%s-%s-%s-%s-%s" % (raw[:8], raw[8:12], raw[12:16], raw[16:20], raw[20:])

def _plain(rich: Any) -> str:
    return "".join(str((part or {}).get("plain_text") or "") for part in (rich or []) if isinstance(part, dict))

def _title(obj: Dict[str, Any]) -> str:
    if obj.get("object") == "database":
        return _plain(obj.get("title")) or "Untitled"
    for prop in (obj.get("properties") or {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return _plain(prop.get("title")) or "Untitled"
    return "Untitled"

def _prop_text(prop: Dict[str, Any]) -> Any:
    kind = prop.get("type")
    value = prop.get(kind)
    if kind in ("title", "rich_text"):
        return _plain(value)
    if kind in ("number", "checkbox", "url", "email", "phone_number", "created_time", "last_edited_time"):
        return value
    if kind in ("select", "status"):
        return (value or {}).get("name")
    if kind == "multi_select":
        return [row.get("name") for row in value or [] if isinstance(row, dict)]
    if kind == "date":
        return value and ((value.get("start") or "") + ((" → " + value["end"]) if value.get("end") else ""))
    if kind == "people":
        return [row.get("name") or row.get("id") for row in value or [] if isinstance(row, dict)]
    if kind == "relation":
        return [row.get("id") for row in value or [] if isinstance(row, dict)]
    if kind == "formula":
        inner = value or {}
        return inner.get(inner.get("type"))
    if kind == "files":
        return [row.get("name") for row in value or [] if isinstance(row, dict)]
    return None

def _block_text(block: Dict[str, Any]) -> str:
    kind = str(block.get("type") or "")
    data = block.get(kind) or {}
    text = _plain(data.get("rich_text"))
    prefix = {"heading_1": "# ", "heading_2": "## ", "heading_3": "### ", "bulleted_list_item": "- ",
              "numbered_list_item": "1. ", "quote": "> ", "callout": "> "}.get(kind, "")
    if kind == "to_do":
        prefix = "- [x] " if data.get("checked") else "- [ ] "
    if kind == "code":
        return "```%s\n%s\n```" % (data.get("language") or "", text)
    if kind == "divider":
        return "---"
    if kind == "child_page":
        return "[page] %s (id %s)" % (data.get("title") or "", block.get("id"))
    if kind == "child_database":
        return "[database] %s (id %s)" % (data.get("title") or "", block.get("id"))
    if kind in ("image", "file", "pdf", "video", "bookmark", "embed"):
        url = (data.get("external") or data.get("file") or {}).get("url") or data.get("url") or ""
        return "[%s] %s %s" % (kind, _plain(data.get("caption")), url if kind in ("bookmark", "embed") else "")
    return prefix + text if (text or prefix) else ""

def _blocks(block_id: str, depth: int, counter: List[int], lines: List[str]) -> None:
    cursor = None
    while counter[0] < MAX_BLOCKS:
        params: Dict[str, Any] = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        data = _request("GET", "/blocks/%s/children" % block_id, params=params)
        for block in data.get("results") or []:
            if not isinstance(block, dict):
                continue
            counter[0] += 1
            line = _block_text(block)
            if line:
                lines.append("  " * depth + line)
            if block.get("has_children") and depth < 2 and block.get("type") not in ("child_page", "child_database"):
                _blocks(str(block.get("id")), depth + 1, counter, lines)
            if counter[0] >= MAX_BLOCKS:
                break
        cursor = data.get("next_cursor")
        if not data.get("has_more") or not cursor:
            break

def _rich(text: str) -> List[Dict[str, Any]]:
    """Notion caps one text object at 2000 characters; **bold** and `code` survive as annotations."""
    out: List[Dict[str, Any]] = []
    for piece in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text):
        if not piece:
            continue
        annotations: Dict[str, bool] = {}
        if piece.startswith("**") and piece.endswith("**") and len(piece) > 4:
            piece, annotations = piece[2:-2], {"bold": True}
        elif piece.startswith("`") and piece.endswith("`") and len(piece) > 2:
            piece, annotations = piece[1:-1], {"code": True}
        for start in range(0, len(piece), 2000):
            row: Dict[str, Any] = {"type": "text", "text": {"content": piece[start:start + 2000]}}
            if annotations:
                row["annotations"] = annotations
            out.append(row)
    return out

def markdown_blocks(text: str) -> List[Dict[str, Any]]:
    """Simple Markdown → Notion blocks: headings, lists, to-dos, quotes, code fences, dividers, paragraphs."""
    blocks: List[Dict[str, Any]] = []
    lines = str(text or "").replace("\r\n", "\n").split("\n")
    i = 0
    paragraph: List[str] = []

    def flush() -> None:
        if paragraph:
            body = " ".join(part.strip() for part in paragraph).strip()
            if body:
                blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rich(body)}})
            paragraph.clear()

    def add(kind: str, body: str, **extra: Any) -> None:
        flush()
        blocks.append({"object": "block", "type": kind, kind: {"rich_text": _rich(body), **extra}})

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        fence = re.match(r"^```(\w*)", stripped)
        if fence:
            flush()
            code: List[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            blocks.append({"object": "block", "type": "code",
                           "code": {"rich_text": [{"type": "text", "text": {"content": chunk}}
                                                  for chunk in _chunks("\n".join(code))] or [],
                                    "language": fence.group(1).lower() or "plain text"}})
            i += 1
            continue
        heading = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        todo = re.match(r"^[-*]\s+\[([ xX])\]\s+(.*)$", stripped)
        if not stripped:
            flush()
        elif heading:
            add("heading_%d" % len(heading.group(1)), heading.group(2))
        elif todo:
            add("to_do", todo.group(2), checked=todo.group(1).lower() == "x")
        elif re.match(r"^[-*•]\s+", stripped):
            add("bulleted_list_item", re.sub(r"^[-*•]\s+", "", stripped))
        elif re.match(r"^\d+[.)]\s+", stripped):
            add("numbered_list_item", re.sub(r"^\d+[.)]\s+", "", stripped))
        elif stripped.startswith(">"):
            add("quote", stripped.lstrip(">").strip())
        elif re.match(r"^(-{3,}|\*{3,})$", stripped):
            flush()
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        else:
            paragraph.append(stripped)
        i += 1
    flush()
    return blocks

def _chunks(text: str) -> List[str]:
    return [text[start:start + 2000] for start in range(0, len(text), 2000)] if text else []

def _append(page_id: str, blocks: List[Dict[str, Any]]) -> int:
    for start in range(0, len(blocks), 100):
        _request("PATCH", "/blocks/%s/children" % page_id, json_body={"children": blocks[start:start + 100]})
    return len(blocks)

def _row(obj: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": obj.get("id"), "type": obj.get("object"), "title": _title(obj), "url": obj.get("url"),
            "last_edited": obj.get("last_edited_time")}

def run(tool: str, args: Dict[str, Any]) -> Any:
    ig = _ig()
    if tool == "notion_search":
        body: Dict[str, Any] = {"page_size": ig._int(args.get("limit"), 10, 1, 50),
                                "sort": {"direction": "descending", "timestamp": "last_edited_time"}}
        if str(args.get("query") or "").strip():
            body["query"] = str(args["query"]).strip()
        kind = str(args.get("type") or "").strip().lower()
        if kind in ("page", "database"):
            body["filter"] = {"property": "object", "value": kind}
        data = _request("POST", "/search", json_body=body)
        rows = [_row(obj) for obj in data.get("results") or [] if isinstance(obj, dict)]
        out: Dict[str, Any] = {"results": rows, "more": bool(data.get("has_more"))}
        if not rows:
            out["note"] = ("Ничего не найдено. Интеграция видит только страницы, к которым ей дан доступ "
                           "(в Notion: «•••» → Connections).")
        return out
    if tool == "notion_read_page":
        page_id = _id(args.get("page_id") or args.get("id"))
        page = _request("GET", "/pages/" + page_id)
        lines: List[str] = []
        _blocks(page_id, 0, [0], lines)
        text = "\n".join(lines)
        props = {name: _prop_text(prop) for name, prop in (page.get("properties") or {}).items()
                 if isinstance(prop, dict) and prop.get("type") != "title"}
        return {"id": page.get("id"), "title": _title(page), "url": page.get("url"),
                "properties": {k: v for k, v in props.items() if v not in (None, "", [])},
                "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT}
    if tool == "notion_query_database":
        database_id = _id(args.get("database_id") or args.get("id"), "database_id")
        body = {"page_size": ig._int(args.get("limit"), 25, 1, 100)}
        if isinstance(args.get("filter"), dict):
            body["filter"] = args["filter"]
        if isinstance(args.get("sorts"), list):
            body["sorts"] = args["sorts"]
        data = _request("POST", "/databases/%s/query" % database_id, json_body=body)
        rows = []
        for obj in data.get("results") or []:
            if not isinstance(obj, dict):
                continue
            props = {name: _prop_text(prop) for name, prop in (obj.get("properties") or {}).items() if isinstance(prop, dict)}
            rows.append({"id": obj.get("id"), "title": _title(obj), "url": obj.get("url"),
                         "properties": {k: v for k, v in props.items() if v not in (None, "", [])}})
        return {"rows": rows, "more": bool(data.get("has_more"))}
    if tool == "notion_create_page":
        title = str(args.get("title") or "").strip()
        if not title:
            raise _err("Укажите title новой страницы", argument=True)
        parent = _id(args.get("parent_id"), "parent_id")
        blocks = markdown_blocks(str(args.get("content") or ""))
        kind = str(args.get("parent_type") or "").strip().lower()
        if kind not in ("page", "database"):
            kind = "page"
            try:
                _request("GET", "/databases/" + parent)
                kind = "database"
            except ig.IntegrationError:
                pass
        if kind == "database":
            schema = _request("GET", "/databases/" + parent)
            title_prop = next((name for name, prop in (schema.get("properties") or {}).items()
                               if isinstance(prop, dict) and prop.get("type") == "title"), "Name")
            properties: Dict[str, Any] = {title_prop: {"title": _rich(title)}}
            if isinstance(args.get("properties"), dict):

                from .integrations_notion_tools import simple_properties
                shaped = simple_properties(schema, {k: v for k, v in args["properties"].items() if k != title_prop})
                properties.update({k: v for k, v in shaped.items() if k != title_prop})
            body = {"parent": {"database_id": parent}, "properties": properties}
        else:
            body = {"parent": {"page_id": parent}, "properties": {"title": {"title": _rich(title)}}}
        body["children"] = blocks[:100]
        page = _request("POST", "/pages", json_body=body)
        if len(blocks) > 100:
            _append(str(page.get("id")), blocks[100:])
        return {"id": page.get("id"), "url": page.get("url"), "title": title, "blocks": len(blocks)}
    if tool == "notion_append":
        page_id = _id(args.get("page_id") or args.get("id"))
        blocks = markdown_blocks(str(args.get("content") or ""))
        if not blocks:
            raise _err("Нечего добавлять: content пуст", argument=True)
        return {"id": page_id, "appended_blocks": _append(page_id, blocks)}

    from . import integrations_notion_tools
    return integrations_notion_tools.run(tool, args)
