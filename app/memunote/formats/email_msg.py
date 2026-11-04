"""E-mail messages (.eml, RFC 822 / MIME) for the read-only mail view.

Only the standard library is used. HTML bodies are never rendered: they are turned into plain text
here, so a message can't run scripts or load remote pictures (tracking pixels) in the editor.
"""
from __future__ import annotations

import html
import re
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MAX_EML = 64 * 1024 * 1024
MAX_BODY = 2 * 1024 * 1024
_BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote", "pre", "hr", "section", "article", "header", "footer"}
_SKIP = {"script", "style", "head", "title", "noscript", "template", "svg"}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        self.skip = 0
        self.href: List[Optional[str]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag in _SKIP:
            self.skip += 1
        elif tag in _BLOCK:
            self.out.append("\n")
        if tag == "li":
            self.out.append("- ")
        if tag == "a":
            href = dict(attrs).get("href") or ""
            self.href.append(href if href.lower().startswith(("http://", "https://", "mailto:")) else None)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK:
            self.out.append("\n")
        if tag == "a" and self.href:
            href = self.href.pop()
            if href and not self.skip:
                text = "".join(self.out[-3:])
                if href.split(":", 1)[-1].strip("/") not in text:
                    self.out.append(f" ({href})")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(re.sub(r"[ \t\r\f\v]+", " ", data.replace("\n", " ")))


def html_to_text(markup: str) -> str:
    parser = _Text()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001 - broken markup: strip the tags the simple way
        return html.unescape(re.sub(r"<[^>]+>", " ", markup))
    text = "".join(parser.out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _addresses(value: Any) -> List[Dict[str, str]]:
    try:
        pairs = getaddresses([str(value)]) if value else []
    except Exception:  # noqa: BLE001
        return [{"name": str(value), "email": ""}]
    return [{"name": name, "email": addr} for name, addr in pairs if name or addr][:200]


def _parts(msg: EmailMessage) -> Tuple[List[Tuple[str, EmailMessage]], List[EmailMessage]]:
    """(body candidates, attachments) in document order."""
    bodies: List[Tuple[str, EmailMessage]] = []
    attachments: List[EmailMessage] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disp = (part.get_content_disposition() or "").lower()
        if disp == "attachment" or (part.get_filename() and disp != "inline") or not ctype.startswith("text/"):
            if ctype.startswith("multipart/"):
                continue
            attachments.append(part)
        elif ctype in ("text/plain", "text/html"):
            bodies.append((ctype, part))
        else:
            attachments.append(part)
    return bodies, attachments


def _content(part: EmailMessage) -> str:
    try:
        value = part.get_content()
    except Exception:  # noqa: BLE001 - unknown charset or damaged transfer encoding
        raw = part.get_payload(decode=True) or b""
        value = raw.decode("utf-8", errors="replace")
    return value if isinstance(value, str) else bytes(value).decode("utf-8", errors="replace")


def parse(data: bytes) -> Dict[str, Any]:
    msg = BytesParser(policy=policy.default).parsebytes(data)
    bodies, attachments = _parts(msg)  # type: ignore[arg-type]
    plain = next((p for c, p in bodies if c == "text/plain"), None)
    rich = next((p for c, p in bodies if c == "text/html"), None)
    if plain is not None:
        text, source = _content(plain), "plain"
    elif rich is not None:
        text, source = html_to_text(_content(rich)), "html"
    else:
        text, source = "", "none"
    date_iso = ""
    try:
        if msg["date"]:
            date_iso = parsedate_to_datetime(str(msg["date"])).isoformat()
    except Exception:  # noqa: BLE001
        date_iso = ""
    items = []
    for index, part in enumerate(attachments):
        payload = part.get_payload(decode=True) or b""
        items.append({"index": index, "name": str(part.get_filename() or f"attachment-{index + 1}"),
                      "type": part.get_content_type(), "size": len(payload),
                      "inline": (part.get_content_disposition() or "") == "inline"})
    return {
        "subject": str(msg["subject"] or ""), "from": _addresses(msg["from"]), "to": _addresses(msg["to"]),
        "cc": _addresses(msg["cc"]), "reply_to": _addresses(msg["reply-to"]), "date": str(msg["date"] or ""),
        "date_iso": date_iso, "text": text[:MAX_BODY], "truncated": len(text) > MAX_BODY, "source": source,
        "has_html": rich is not None, "attachments": items[:500],
    }


def read_eml(path: str) -> Dict[str, Any]:
    p = Path(path)
    if p.stat().st_size > MAX_EML:
        raise ValueError("the message is too large")
    return {"email": parse(p.read_bytes())}


def attachment(path: str, index: int) -> Tuple[str, str, bytes]:
    p = Path(path)
    if p.stat().st_size > MAX_EML:
        raise ValueError("the message is too large")
    msg = BytesParser(policy=policy.default).parsebytes(p.read_bytes())
    _, attachments = _parts(msg)  # type: ignore[arg-type]
    if not 0 <= index < len(attachments):
        raise IndexError("no such attachment")
    part = attachments[index]
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", str(part.get_filename() or f"attachment-{index + 1}")).strip(" .") or f"attachment-{index + 1}"
    return name[:200], part.get_content_type(), part.get_payload(decode=True) or b""
