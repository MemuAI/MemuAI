"""Gmail tools of the assistant — through the shared Google account (Round 30; full set since Round 33).

Called by :func:`app.integrations.run_tool` for ``gmail_*``; the HTTP call, the token refresh, the scope
pre-check and the error texts are :func:`app.integrations._google`.

Round 30 had search / read / drafts / send.  Round 33 (user report: «MemuAI в Gmail не может удалять письма, да и
в целом многое ему недоступно») adds threads, labels, archive, read/unread, stars, trash / untrash / permanent
delete, bulk actions by a search query, replies (incl. reply-all) and forwards with attachments, attachments from
files on this computer, saving attachments, filters and the vacation responder.

Writes ask the user first (``integrations.WRITE_TOOLS``); sending, forwarding and permanent deletion ask **always**,
even in «Full access» (``integrations.ALWAYS_CONFIRM``) — they cannot be taken back.

Scopes (``google_account.SERVICES["gmail"]``): ``https://mail.google.com/`` (everything, incl. permanent delete)
+ ``gmail.settings.basic`` (filters, vacation).  Older grants (``gmail.readonly`` + ``gmail.compose``) keep reading
and sending; the rest asks to «Expand access» (``integrations.TOOL_SCOPES``).
"""
from __future__ import annotations

import base64
import html
import mimetypes
import os
import re
from email.message import EmailMessage
from email.utils import getaddresses
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
GMAIL_UPLOAD = "https://gmail.googleapis.com/upload/gmail/v1/users/me"
MAX_BODY = 60000
#: Above this the letter goes through the upload endpoint (the plain JSON body is meant for small messages).
UPLOAD_OVER = 3 * 1024 * 1024
#: Gmail refuses letters over 25 MB of attachments (35 MB on the wire after base64).
MAX_ATTACH = 25 * 1024 * 1024
MAX_BULK = 500
_ADDRESS = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")
HEADERS = ("From", "To", "Cc", "Subject", "Date")
SYSTEM_LABELS = {"INBOX", "SPAM", "TRASH", "UNREAD", "STARRED", "IMPORTANT", "SENT", "DRAFT", "CHAT",
                 "CATEGORY_PERSONAL", "CATEGORY_SOCIAL", "CATEGORY_PROMOTIONS", "CATEGORY_UPDATES", "CATEGORY_FORUMS"}
#: Friendly names the model may use for system labels.
LABEL_ALIASES = {"inbox": "INBOX", "входящие": "INBOX", "spam": "SPAM", "спам": "SPAM", "trash": "TRASH",
                 "корзина": "TRASH", "unread": "UNREAD", "непрочитанные": "UNREAD", "starred": "STARRED",
                 "помеченные": "STARRED", "important": "IMPORTANT", "важные": "IMPORTANT", "sent": "SENT",
                 "отправленные": "SENT", "draft": "DRAFT", "drafts": "DRAFT", "черновики": "DRAFT",
                 "promotions": "CATEGORY_PROMOTIONS", "social": "CATEGORY_SOCIAL", "updates": "CATEGORY_UPDATES",
                 "forums": "CATEGORY_FORUMS", "personal": "CATEGORY_PERSONAL"}

TOOLS: List[str] = [
    "gmail_search{query,limit,page_token,include_spam_trash}",
    "gmail_read{message_id}",
    "gmail_read_thread{thread_id}",
    "gmail_create_draft{to,subject,body,cc,bcc,html,attachments,reply_to_message_id,reply_all}",
    "gmail_update_draft{draft_id,to,subject,body,cc,bcc,html,attachments}",
    "gmail_list_drafts{limit}",
    "gmail_delete_draft{draft_id}",
    "gmail_send{to,subject,body,cc,bcc,html,attachments,reply_to_message_id,reply_all,draft_id}",
    "gmail_reply{message_id,body,html,reply_all,attachments,draft}",
    "gmail_forward{message_id,to,cc,body,include_attachments,draft}",
    "gmail_archive{message_ids,thread_id}",
    "gmail_mark_read{message_ids,thread_id,read}",
    "gmail_star{message_ids,starred}",
    "gmail_modify{message_ids,thread_id,add_labels,remove_labels}",
    "gmail_trash{message_ids,thread_id}",
    "gmail_untrash{message_ids,thread_id}",
    "gmail_delete{message_ids,thread_id}",
    "gmail_bulk{query,action,label,limit}",
    "gmail_list_labels{}",
    "gmail_create_label{name}",
    "gmail_update_label{label,new_name}",
    "gmail_delete_label{label}",
    "gmail_save_attachment{message_id,attachment_id,name,folder}",
    "gmail_list_filters{}",
    "gmail_create_filter{from,to,subject,query,has_attachment,add_labels,archive,mark_read,star,trash,never_spam,forward}",
    "gmail_delete_filter{filter_id}",
    "gmail_vacation{enable,subject,body,start,end,contacts_only}",
]

READ = {"gmail_search", "gmail_read", "gmail_read_thread", "gmail_list_drafts", "gmail_list_labels", "gmail_list_filters"}
WRITE = {name.split("{")[0] for name in TOOLS} - READ
#: Sent or destroyed for good — asked every time, even with «Full access».
ALWAYS = {"gmail_send", "gmail_reply", "gmail_forward", "gmail_delete"}
DANGER = {"gmail_delete", "gmail_bulk", "gmail_delete_label", "gmail_delete_filter", "gmail_trash"}
BULK_ACTIONS = ("archive", "trash", "untrash", "read", "unread", "star", "unstar", "label", "unlabel", "spam", "not_spam")

HINT = ("Gmail: gmail_search uses Gmail syntax (from:, to:, subject:, newer_than:7d, older_than:1y, is:unread, "
        "has:attachment, label:x, in:trash) and returns message ids + thread ids. Acting on found mail: gmail_archive, "
        "gmail_mark_read{read:false} for unread, gmail_star, gmail_modify (labels by name), gmail_trash (recoverable for "
        "30 days), gmail_delete (PERMANENT — only when the user explicitly says delete forever), gmail_bulk{query,action} "
        "for many letters at once. gmail_reply keeps the thread; attachments are full paths of files on this computer.")


def _api(method: str, path: str, **kwargs: Any) -> Any:
    from .integrations import _google
    return _google("gmail", method, GMAIL_API + path, **kwargs)


def _upload(path: str, metadata: Dict[str, Any], letter: bytes) -> Any:
    from .integrations import _google, _multipart
    body, content_type = _multipart(metadata, letter, "message/rfc822")
    return _google("gmail", "POST", GMAIL_UPLOAD + path, params={"uploadType": "multipart"},
                   content=body, content_type=content_type)


def _arg_error(text: str):
    from .integrations import IntegrationArgumentError
    return IntegrationArgumentError(text)


def _headers(payload: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for row in payload.get("headers") or []:
        if isinstance(row, dict) and row.get("name"):
            out.setdefault(str(row["name"]).lower(), str(row.get("value") or ""))
    return out


def _decode_part(data: str) -> str:
    raw = str(data or "")
    try:
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", "replace")
    except Exception:
        return ""


def _decode_bytes(data: str) -> bytes:
    raw = str(data or "")
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def _html_text(markup: str) -> str:
    text = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", markup)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def _walk(part: Dict[str, Any], plain: List[str], rich: List[str], files: List[Dict[str, Any]]) -> None:
    mime = str(part.get("mimeType") or "")
    body = part.get("body") or {}
    name = str(part.get("filename") or "")
    if name:
        files.append({"name": name, "mime": mime, "size": int(body.get("size") or 0),
                      "attachment_id": str(body.get("attachmentId") or ""), "_data": body.get("data")})
    elif mime == "text/plain" and body.get("data"):
        plain.append(_decode_part(body["data"]))
    elif mime == "text/html" and body.get("data"):
        rich.append(_html_text(_decode_part(body["data"])))
    for child in part.get("parts") or []:
        if isinstance(child, dict):
            _walk(child, plain, rich, files)


def _summary(row: Dict[str, Any]) -> Dict[str, Any]:
    head = _headers(row.get("payload") or {})
    labels = row.get("labelIds") or []
    return {"id": row.get("id"), "thread_id": row.get("threadId"), "from": head.get("from", ""),
            "to": head.get("to", ""), "subject": head.get("subject", ""), "date": head.get("date", ""),
            "snippet": html.unescape(str(row.get("snippet") or "")), "unread": "UNREAD" in labels,
            "starred": "STARRED" in labels, "labels": [label for label in labels if label not in ("UNREAD", "STARRED")]}


def _full(row: Dict[str, Any]) -> Dict[str, Any]:
    plain: List[str] = []
    rich: List[str] = []
    files: List[Dict[str, Any]] = []
    payload = row.get("payload") or {}
    _walk(payload, plain, rich, files)
    text = "\n\n".join(part.strip() for part in plain if part.strip()) or "\n\n".join(rich)
    out = _summary(row)
    out.update({"cc": _headers(payload).get("cc", ""), "text": text[:MAX_BODY], "truncated": len(text) > MAX_BODY,
                "attachments": [{k: v for k, v in item.items() if not k.startswith("_")} for item in files]})
    return out


def _addresses(value: Any, field: str, required: bool = False) -> str:
    """«a@b.c, Имя <d@e.f>» → validated header value; the model's typos fail before anything is sent."""
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(item) for item in value)
    text = str(value or "").strip()
    if not text:
        if required:
            raise _arg_error("Укажите %s — адрес получателя" % field)
        return ""
    pairs = getaddresses([text])
    bad = [addr or name for name, addr in pairs if not _ADDRESS.match(addr or "")]
    if bad or not pairs:
        raise _arg_error("Неверный адрес в %s: %s" % (field, ", ".join(bad) or text))
    return text


def _paths(value: Any) -> List[Path]:
    """attachments: full paths of files on this computer (a list or «a; b»)."""
    if not value:
        return []
    items = value if isinstance(value, (list, tuple)) else re.split(r"[;\n]", str(value))
    out: List[Path] = []
    total = 0
    for item in items:
        raw = str(item.get("path") if isinstance(item, dict) else item).strip().strip('"')
        if not raw:
            continue
        path = Path(os.path.expandvars(os.path.expanduser(raw)))
        if not path.is_absolute() or not path.is_file():
            raise _arg_error("Вложение не найдено: %s (нужен полный путь к файлу на этом компьютере)" % raw)
        total += path.stat().st_size
        if total > MAX_ATTACH:
            raise _arg_error("Вложения больше 25 МБ — Gmail их не примет. Загрузите большой файл на Google Диск и отправьте ссылку.")
        out.append(path)
    return out


def _attach(letter: EmailMessage, name: str, data: bytes, mime: str = "") -> None:
    kind = (mime or mimetypes.guess_type(name)[0] or "application/octet-stream").split("/", 1)
    if len(kind) != 2:
        kind = ["application", "octet-stream"]
    letter.add_attachment(data, maintype=kind[0], subtype=kind[1], filename=name)


def _me() -> str:
    try:
        return str(_api("GET", "/profile").get("emailAddress") or "").lower()
    except Exception:
        return ""


def _message(args: Dict[str, Any], extra: Optional[List[Tuple[str, bytes, str]]] = None) -> Tuple[EmailMessage, str]:
    """The RFC 2822 letter (UTF-8) + threadId; a reply keeps the thread (In-Reply-To / References / «Re:»)."""
    letter = EmailMessage()
    reply_id = str(args.get("reply_to_message_id") or "").strip()
    to = _addresses(args.get("to"), "to", required=not reply_id)
    cc = _addresses(args.get("cc"), "cc")
    bcc = _addresses(args.get("bcc"), "bcc")
    subject = str(args.get("subject") or "").strip()
    thread = ""
    if reply_id:
        original = _api("GET", "/messages/" + quote(reply_id, safe=""),
                        params={"format": "metadata",
                                "metadataHeaders": ["Message-ID", "References", "Subject", "From", "Reply-To", "To", "Cc"]})
        head = _headers(original.get("payload") or {})
        thread = str(original.get("threadId") or "")
        message_id = head.get("message-id", "")
        if message_id:
            letter["In-Reply-To"] = message_id
            letter["References"] = (head.get("references", "") + " " + message_id).strip()
        if not subject:
            base = head.get("subject", "")
            subject = base if base.lower().startswith("re:") else ("Re: " + base).strip()
        if not to:
            to = head.get("reply-to") or head.get("from", "")
        if str(args.get("reply_all") or "").lower() in ("1", "true", "yes") or args.get("reply_all") is True:
            me = _me()
            seen = {addr.lower() for _n, addr in getaddresses([to])} | ({me} if me else set())
            others = []
            for name, addr in getaddresses([head.get("to", ""), head.get("cc", ""), cc]):
                if addr and addr.lower() not in seen:
                    seen.add(addr.lower())
                    others.append("%s <%s>" % (name, addr) if name else addr)
            cc = ", ".join(others)
    if to:
        letter["To"] = to
    if cc:
        letter["Cc"] = cc
    if bcc:
        letter["Bcc"] = bcc
    letter["Subject"] = subject
    body = str(args.get("body") or "")
    letter.set_content(body)
    if str(args.get("html") or "").strip():
        letter.add_alternative(str(args["html"]), subtype="html")
    for path in _paths(args.get("attachments")):
        _attach(letter, path.name, path.read_bytes())
    for name, data, mime in extra or []:
        _attach(letter, name, data, mime)
    return letter, thread


def _raw(letter: EmailMessage) -> str:
    return base64.urlsafe_b64encode(letter.as_bytes()).decode("ascii")


def _send(letter: EmailMessage, thread: str) -> Dict[str, Any]:
    data = letter.as_bytes()
    if len(data) > UPLOAD_OVER:
        return _upload("/messages/send", {"threadId": thread} if thread else {}, data)
    body: Dict[str, Any] = {"raw": _raw(letter)}
    if thread:
        body["threadId"] = thread
    return _api("POST", "/messages/send", json_body=body)


def _draft(letter: EmailMessage, thread: str, draft_id: str = "") -> Dict[str, Any]:
    data = letter.as_bytes()
    method, path = ("PUT", "/drafts/" + quote(draft_id, safe="")) if draft_id else ("POST", "/drafts")
    message: Dict[str, Any] = {"threadId": thread} if thread else {}
    if len(data) > UPLOAD_OVER:
        from .integrations import _google, _multipart
        payload: Dict[str, Any] = {"message": message}
        if draft_id:
            payload["id"] = draft_id
        body, content_type = _multipart(payload, data, "message/rfc822")
        return _google("gmail", method, GMAIL_UPLOAD + path, params={"uploadType": "multipart"},
                       content=body, content_type=content_type)
    message["raw"] = _raw(letter)
    payload = {"message": message}
    if draft_id:
        payload["id"] = draft_id
    return _api(method, path, json_body=payload)


# --------------------------------------------------------------------------- ids, labels
def _ids(args: Dict[str, Any], key: str = "message_ids") -> List[str]:
    value = args.get(key)
    if value is None:
        value = args.get("message_id") or args.get("ids") or args.get("id")
    items = value if isinstance(value, (list, tuple)) else re.split(r"[,\s]+", str(value or ""))
    out = []
    for item in items:
        text = str(item).strip()
        if text and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", text) and text not in out:
            out.append(text)
    return out[:1000]


def _thread(args: Dict[str, Any]) -> str:
    text = str(args.get("thread_id") or "").strip()
    if text and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", text):
        raise _arg_error("Неверный thread_id: %s" % text)
    return text


class _Labels:
    """Label names ↔ ids for one tool call (one listing at most)."""

    def __init__(self) -> None:
        self.rows: Optional[List[Dict[str, Any]]] = None

    def all(self) -> List[Dict[str, Any]]:
        if self.rows is None:
            self.rows = [row for row in (_api("GET", "/labels").get("labels") or []) if isinstance(row, dict)]
        return self.rows

    def find(self, name: str) -> Optional[Dict[str, Any]]:
        text = str(name or "").strip()
        upper = LABEL_ALIASES.get(text.lower(), text.upper())
        if upper in SYSTEM_LABELS:
            return {"id": upper, "name": upper, "type": "system"}
        for row in self.all():
            if row.get("id") == text or str(row.get("name") or "").lower() == text.lower():
                return row
        return None

    def ids(self, names: Any, create: bool = False) -> List[str]:
        items = names if isinstance(names, (list, tuple)) else [s for s in re.split(r"[,\n]", str(names or ""))]
        out = []
        for name in items:
            text = str(name).strip()
            if not text:
                continue
            row = self.find(text)
            if row is None and create:
                row = _api("POST", "/labels", json_body={"name": text, "labelListVisibility": "labelShow",
                                                         "messageListVisibility": "show"})
                self.rows = None
            if row is None:
                raise _arg_error("Метка не найдена: %s (gmail_list_labels покажет все; новую создаст gmail_create_label)" % text)
            out.append(str(row["id"]))
        return out


def _modify(args: Dict[str, Any], add: List[str], remove: List[str]) -> Dict[str, Any]:
    """Apply label changes to messages (batchModify) or to a whole thread."""
    thread = _thread(args)
    if thread:
        _api("POST", "/threads/%s/modify" % quote(thread, safe=""), json_body={"addLabelIds": add, "removeLabelIds": remove})
        return {"thread_id": thread, "added": add, "removed": remove}
    ids = _ids(args)
    if not ids:
        raise _arg_error("Укажите message_ids (их даёт gmail_search) или thread_id")
    _api("POST", "/messages/batchModify", json_body={"ids": ids, "addLabelIds": add, "removeLabelIds": remove})
    return {"messages": len(ids), "added": add, "removed": remove}


def _each(args: Dict[str, Any], action: str) -> Dict[str, Any]:
    """trash / untrash: one call per message (Gmail has no batch for them), or the whole thread."""
    thread = _thread(args)
    if thread:
        _api("POST", "/threads/%s/%s" % (quote(thread, safe=""), action))
        return {"thread_id": thread, action: True}
    ids = _ids(args)
    if not ids:
        raise _arg_error("Укажите message_ids (их даёт gmail_search) или thread_id")
    if len(ids) > 100:
        _api("POST", "/messages/batchModify", json_body={"ids": ids, "addLabelIds": ["TRASH"] if action == "trash" else [],
                                                          "removeLabelIds": ["TRASH"] if action == "untrash" else []})
    else:
        for message in ids:
            _api("POST", "/messages/%s/%s" % (quote(message, safe=""), action))
    return {"messages": len(ids), action: True}


def _search_ids(query: str, limit: int) -> List[str]:
    ids: List[str] = []
    token = ""
    while len(ids) < limit:
        params: Dict[str, Any] = {"q": query, "maxResults": min(500, limit - len(ids))}
        if token:
            params["pageToken"] = token
        page = _api("GET", "/messages", params=params)
        ids += [str(row["id"]) for row in page.get("messages") or [] if isinstance(row, dict) and row.get("id")]
        token = str(page.get("nextPageToken") or "")
        if not token:
            break
    return ids[:limit]


def _save_dir(folder: str) -> Path:
    if folder:
        path = Path(os.path.expandvars(os.path.expanduser(folder)))
        if not path.is_absolute():
            raise _arg_error("folder — полный путь к папке на этом компьютере")
    else:
        path = Path.home() / "Downloads" / "MemuAI"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(name: str) -> str:
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", str(name or "").strip()).strip(" .") or "attachment"
    if text.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *("COM%d" % i for i in range(1, 10)), *("LPT%d" % i for i in range(1, 10))}:
        text = "_" + text
    return text[:180]


def _attachment_bytes(message_id: str, attachment_id: str, inline: Any = None) -> bytes:
    if inline:
        return _decode_bytes(inline)
    row = _api("GET", "/messages/%s/attachments/%s" % (quote(message_id, safe=""), quote(attachment_id, safe="")))
    return _decode_bytes(row.get("data") or "")


def _bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on", "да")


# --------------------------------------------------------------------------- the tools
def run(tool: str, args: Dict[str, Any]) -> Any:  # noqa: C901 - one flat dispatch table
    from .integrations import _int
    if tool == "gmail_search":
        limit = _int(args.get("limit"), 10, 1, 50)
        params: Dict[str, Any] = {"maxResults": limit}
        query = str(args.get("query") or "").strip()
        if query:
            params["q"] = query
        if args.get("page_token"):
            params["pageToken"] = str(args["page_token"])
        if _bool(args.get("include_spam_trash")):
            params["includeSpamTrash"] = "true"
        listing = _api("GET", "/messages", params=params)
        rows = []
        for item in (listing.get("messages") or [])[:limit]:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            row = _api("GET", "/messages/" + quote(str(item["id"]), safe=""),
                       params={"format": "metadata", "metadataHeaders": list(HEADERS)})
            rows.append(_summary(row))
        return {"messages": rows, "next_page_token": listing.get("nextPageToken") or "",
                "estimate": listing.get("resultSizeEstimate")}
    if tool == "gmail_read":
        message_id = str(args.get("message_id") or args.get("id") or "").strip()
        if not message_id:
            raise _arg_error("Укажите message_id (его даёт gmail_search)")
        return _full(_api("GET", "/messages/" + quote(message_id, safe=""), params={"format": "full"}))
    if tool == "gmail_read_thread":
        thread = _thread(args)
        if not thread:
            raise _arg_error("Укажите thread_id (его даёт gmail_search)")
        row = _api("GET", "/threads/" + quote(thread, safe=""), params={"format": "full"})
        messages = [_full(m) for m in row.get("messages") or [] if isinstance(m, dict)]
        budget = MAX_BODY
        for item in messages:     # one long thread must not blow the context: older letters are cut first
            item["text"] = item["text"][:max(1500, budget // max(1, len(messages)))]
        return {"thread_id": thread, "count": len(messages), "messages": messages}
    if tool == "gmail_create_draft":
        letter, thread = _message(args)
        draft = _draft(letter, thread)
        return {"draft_id": draft.get("id"), "message_id": (draft.get("message") or {}).get("id"),
                "note": "Черновик сохранён в Gmail — отправить его можно gmail_send{draft_id} или из Gmail."}
    if tool == "gmail_update_draft":
        draft_id = str(args.get("draft_id") or "").strip()
        if not draft_id:
            raise _arg_error("Укажите draft_id (его даёт gmail_list_drafts или gmail_create_draft)")
        letter, thread = _message(args)
        draft = _draft(letter, thread, draft_id)
        return {"draft_id": draft.get("id") or draft_id, "updated": True}
    if tool == "gmail_list_drafts":
        limit = _int(args.get("limit"), 20, 1, 50)
        listing = _api("GET", "/drafts", params={"maxResults": limit})
        rows = []
        for item in (listing.get("drafts") or [])[:limit]:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            row = _api("GET", "/drafts/" + quote(str(item["id"]), safe=""), params={"format": "metadata"})
            rows.append({"draft_id": row.get("id"), **_summary(row.get("message") or {})})
        return {"drafts": rows}
    if tool == "gmail_delete_draft":
        draft_id = str(args.get("draft_id") or "").strip()
        if not draft_id:
            raise _arg_error("Укажите draft_id")
        _api("DELETE", "/drafts/" + quote(draft_id, safe=""))
        return {"draft_id": draft_id, "deleted": True}
    if tool == "gmail_send":
        draft_id = str(args.get("draft_id") or "").strip()
        if draft_id:
            sent = _api("POST", "/drafts/send", json_body={"id": draft_id})
        else:
            letter, thread = _message(args)
            sent = _send(letter, thread)
        return {"sent": True, "message_id": sent.get("id"), "thread_id": sent.get("threadId")}
    if tool == "gmail_reply":
        message_id = str(args.get("message_id") or args.get("reply_to_message_id") or "").strip()
        if not message_id:
            raise _arg_error("Укажите message_id письма, на которое отвечаем")
        letter, thread = _message({**args, "reply_to_message_id": message_id, "to": args.get("to") or ""})
        if _bool(args.get("draft")):
            draft = _draft(letter, thread)
            return {"draft_id": draft.get("id"), "thread_id": thread, "note": "Ответ сохранён черновиком."}
        sent = _send(letter, thread)
        return {"sent": True, "message_id": sent.get("id"), "thread_id": sent.get("threadId") or thread}
    if tool == "gmail_forward":
        message_id = str(args.get("message_id") or "").strip()
        if not message_id:
            raise _arg_error("Укажите message_id письма для пересылки")
        original = _api("GET", "/messages/" + quote(message_id, safe=""), params={"format": "full"})
        info = _full(original)
        payload = original.get("payload") or {}
        plain: List[str] = []
        rich: List[str] = []
        files: List[Dict[str, Any]] = []
        _walk(payload, plain, rich, files)
        quoted = ("\n\n---------- Forwarded message ---------\nFrom: %s\nDate: %s\nSubject: %s\nTo: %s\n\n%s"
                  % (info["from"], info["date"], info["subject"], info["to"], info["text"]))
        subject = info["subject"] if info["subject"].lower().startswith(("fwd:", "fw:")) else ("Fwd: " + info["subject"]).strip()
        extra: List[Tuple[str, bytes, str]] = []
        if _bool(args.get("include_attachments"), True):
            total = 0
            for item in files:
                data = _attachment_bytes(message_id, item["attachment_id"], item.get("_data"))
                total += len(data)
                if total > MAX_ATTACH:
                    raise _arg_error("Вложения письма больше 25 МБ — перешлите без вложений (include_attachments: false)")
                extra.append((item["name"], data, item["mime"]))
        letter, _thread_id = _message({"to": args.get("to"), "cc": args.get("cc"), "subject": subject,
                                       "body": str(args.get("body") or "").rstrip() + quoted}, extra)
        if _bool(args.get("draft")):
            draft = _draft(letter, "")
            return {"draft_id": draft.get("id"), "attachments": len(extra), "note": "Пересылка сохранена черновиком."}
        sent = _send(letter, "")
        return {"sent": True, "forwarded": message_id, "attachments": len(extra), "message_id": sent.get("id")}
    if tool == "gmail_archive":
        return {"archived": True, **_modify(args, [], ["INBOX"])}
    if tool == "gmail_mark_read":
        read = _bool(args.get("read"), True)
        return {"read": read, **_modify(args, [] if read else ["UNREAD"], ["UNREAD"] if read else [])}
    if tool == "gmail_star":
        starred = _bool(args.get("starred"), True)
        return {"starred": starred, **_modify(args, ["STARRED"] if starred else [], [] if starred else ["STARRED"])}
    if tool == "gmail_modify":
        labels = _Labels()
        add = labels.ids(args.get("add_labels"), create=True)
        remove = labels.ids(args.get("remove_labels"))
        if not add and not remove:
            raise _arg_error("Укажите add_labels и/или remove_labels (имена меток)")
        return _modify(args, add, remove)
    if tool == "gmail_trash":
        return _each(args, "trash")
    if tool == "gmail_untrash":
        return _each(args, "untrash")
    if tool == "gmail_delete":
        thread = _thread(args)
        if thread:
            _api("DELETE", "/threads/" + quote(thread, safe=""))
            return {"thread_id": thread, "deleted_forever": True}
        ids = _ids(args)
        if not ids:
            raise _arg_error("Укажите message_ids или thread_id")
        _api("POST", "/messages/batchDelete", json_body={"ids": ids})
        return {"messages": len(ids), "deleted_forever": True}
    if tool == "gmail_bulk":
        query = str(args.get("query") or "").strip()
        action = str(args.get("action") or "").strip().lower().replace("-", "_")
        if not query:
            raise _arg_error("Укажите query — какие письма (синтаксис поиска Gmail)")
        if action not in BULK_ACTIONS:
            raise _arg_error("action: " + ", ".join(BULK_ACTIONS))
        limit = _int(args.get("limit"), 100, 1, MAX_BULK)
        ids = _search_ids(query + (" in:trash" if action == "untrash" and "in:trash" not in query else ""), limit)
        if not ids:
            return {"matched": 0, "action": action, "note": "Ничего не найдено по запросу."}
        add: List[str] = []
        remove: List[str] = []
        if action == "archive":
            remove = ["INBOX"]
        elif action == "trash":
            add = ["TRASH"]
        elif action == "untrash":
            remove = ["TRASH"]
        elif action == "read":
            remove = ["UNREAD"]
        elif action == "unread":
            add = ["UNREAD"]
        elif action == "star":
            add = ["STARRED"]
        elif action == "unstar":
            remove = ["STARRED"]
        elif action == "spam":
            add, remove = ["SPAM"], ["INBOX"]
        elif action == "not_spam":
            add, remove = ["INBOX"], ["SPAM"]
        else:
            label = str(args.get("label") or "").strip()
            if not label:
                raise _arg_error("Для label/unlabel укажите label")
            ids_ = _Labels().ids([label], create=action == "label")
            add, remove = (ids_, []) if action == "label" else ([], ids_)
        for start in range(0, len(ids), 1000):
            _api("POST", "/messages/batchModify", json_body={"ids": ids[start:start + 1000], "addLabelIds": add, "removeLabelIds": remove})
        return {"matched": len(ids), "action": action, "limited": len(ids) >= limit,
                "note": ("Обработано первые %d — повторите для остальных." % limit) if len(ids) >= limit else ""}
    if tool == "gmail_list_labels":
        rows = _Labels().all()
        return {"labels": [{"id": row.get("id"), "name": row.get("name"), "type": row.get("type")} for row in rows]}
    if tool == "gmail_create_label":
        name = str(args.get("name") or "").strip()
        if not name:
            raise _arg_error("Укажите name метки (вложенная — через «/»: Работа/Отчёты)")
        row = _api("POST", "/labels", json_body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"})
        return {"created": True, "id": row.get("id"), "name": row.get("name")}
    if tool in ("gmail_update_label", "gmail_delete_label"):
        row = _Labels().find(str(args.get("label") or args.get("label_id") or ""))
        if not row:
            raise _arg_error("Метка не найдена: %s" % (args.get("label") or ""))
        if row.get("type") == "system":
            raise _arg_error("Системные метки (%s) нельзя менять или удалять" % row["id"])
        if tool == "gmail_delete_label":
            _api("DELETE", "/labels/" + quote(str(row["id"]), safe=""))
            return {"deleted": True, "label": row.get("name")}
        new = str(args.get("new_name") or "").strip()
        if not new:
            raise _arg_error("Укажите new_name")
        updated = _api("PATCH", "/labels/" + quote(str(row["id"]), safe=""), json_body={"name": new})
        return {"updated": True, "id": updated.get("id"), "name": updated.get("name")}
    if tool == "gmail_save_attachment":
        message_id = str(args.get("message_id") or "").strip()
        if not message_id:
            raise _arg_error("Укажите message_id (и attachment_id или name из gmail_read)")
        original = _api("GET", "/messages/" + quote(message_id, safe=""), params={"format": "full"})
        plain: List[str] = []
        rich: List[str] = []
        files: List[Dict[str, Any]] = []
        _walk(original.get("payload") or {}, plain, rich, files)
        wanted_id = str(args.get("attachment_id") or "").strip()
        wanted_name = str(args.get("name") or "").strip().lower()
        chosen = [f for f in files if (wanted_id and f["attachment_id"] == wanted_id) or (wanted_name and f["name"].lower() == wanted_name)] \
            if (wanted_id or wanted_name) else files
        if not chosen:
            raise _arg_error("Вложение не найдено в письме (gmail_read покажет список вложений)")
        folder = _save_dir(str(args.get("folder") or ""))
        saved = []
        for item in chosen[:20]:
            data = _attachment_bytes(message_id, item["attachment_id"], item.get("_data"))
            target = folder / _safe_name(item["name"])
            stem, suffix, n = target.stem, target.suffix, 1
            while target.exists():
                n += 1
                target = folder / ("%s (%d)%s" % (stem, n, suffix))
            target.write_bytes(data)
            saved.append({"name": item["name"], "path": str(target), "size": len(data)})
        return {"saved": saved}
    if tool == "gmail_list_filters":
        rows = _api("GET", "/settings/filters").get("filter") or []
        return {"filters": [{"id": row.get("id"), "criteria": row.get("criteria"), "action": row.get("action")} for row in rows]}
    if tool == "gmail_create_filter":
        criteria = {key: str(args[key]).strip() for key in ("from", "to", "subject", "query") if str(args.get(key) or "").strip()}
        if _bool(args.get("has_attachment")):
            criteria["hasAttachment"] = True
        if not criteria:
            raise _arg_error("Укажите условие: from, to, subject, query или has_attachment")
        labels = _Labels()
        add = labels.ids(args.get("add_labels"), create=True)
        remove: List[str] = []
        if _bool(args.get("archive")):
            remove.append("INBOX")
        if _bool(args.get("mark_read")):
            remove.append("UNREAD")
        if _bool(args.get("star")):
            add.append("STARRED")
        if _bool(args.get("trash")):
            add.append("TRASH")
        if _bool(args.get("never_spam")):
            remove.append("SPAM")
        action: Dict[str, Any] = {}
        if add:
            action["addLabelIds"] = add
        if remove:
            action["removeLabelIds"] = remove
        if str(args.get("forward") or "").strip():
            action["forward"] = _addresses(args.get("forward"), "forward")
        if not action:
            raise _arg_error("Укажите действие: add_labels, archive, mark_read, star, trash, never_spam или forward")
        row = _api("POST", "/settings/filters", json_body={"criteria": criteria, "action": action})
        return {"created": True, "filter_id": row.get("id"), "criteria": criteria, "action": action}
    if tool == "gmail_delete_filter":
        filter_id = str(args.get("filter_id") or "").strip()
        if not filter_id:
            raise _arg_error("Укажите filter_id (его даёт gmail_list_filters)")
        _api("DELETE", "/settings/filters/" + quote(filter_id, safe=""))
        return {"deleted": True, "filter_id": filter_id}
    if tool == "gmail_vacation":
        enable = _bool(args.get("enable"), True)
        body: Dict[str, Any] = {"enableAutoReply": enable}
        if enable:
            subject, text = str(args.get("subject") or "").strip(), str(args.get("body") or "").strip()
            if not text:
                raise _arg_error("Укажите body — текст автоответа")
            body.update({"responseSubject": subject, "responseBodyPlainText": text,
                         "restrictToContacts": _bool(args.get("contacts_only"))})
            from datetime import datetime
            for key, field in (("start", "startTime"), ("end", "endTime")):
                value = str(args.get(key) or "").strip()
                if value:
                    try:
                        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    except ValueError as exc:
                        raise _arg_error("Неверная дата %s: %s (ISO-8601)" % (key, value)) from exc
                    body[field] = str(int((moment if moment.tzinfo else moment.astimezone()).timestamp() * 1000))
        row = _api("PUT", "/settings/vacation", json_body=body)
        return {"vacation": bool(row.get("enableAutoReply", enable)), "subject": row.get("responseSubject")}
    raise _arg_error("Неизвестный инструмент Gmail: %s" % tool)
