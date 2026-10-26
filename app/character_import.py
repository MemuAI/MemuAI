# -*- coding: utf-8 -*-
"""Round 29 (phase 2): «Import from a link» for modules — the ``net-import`` permission.

Modules have no network on purpose (CSP ``connect-src 'none'``). This is the one generic, narrow door:
a module that declares the ``net-import`` permission may ask MemuAI to download a *character card or a
lorebook* from a link or an id, exactly like SillyTavern's «Import from external URL»
(``src/endpoints/content-manager.js``: ``/importURL`` + ``/importUUID``). The download runs HERE, in the
MemuAI process (not in the Proxy), and never in the module.

Sources (host-specific resolvers, the same endpoints SillyTavern uses):

=============  ======================================================================================
chub           ``Creator/slug`` or a chub.ai / characterhub.org link -> ``api.chub.ai`` metadata + avatar
chub-lorebook  ``lorebooks/creator/slug`` or a chub lorebook link -> ``sillytavern_raw.json`` of the project
janitor        ``<uuid>_character-…`` or a janitorai.com / jannyai.com link -> ``api.jannyai.com`` PNG
pygmalion      ``<uuid>`` or a pygmalion.chat link -> ``server.pygmalion.chat`` V2 export + avatar
aicc           ``AICC/author/card`` or an aicharactercards.com link -> its PNG API
risu           a realm.risuai.net/character/<id> link -> the V3 PNG download
perchance      ``Name~<32 hex>.gz`` or a perchance.org/ai-character-chat?data=… link -> user.uploads.dev
generic        a direct HTTPS link to a PNG/JSON on an allowed host (``whitelistImportDomains`` of ST)
=============  ======================================================================================

Security (README_FOR_AI §3, §14):

* only public HTTPS on port 443, no credentials in the URL; every address the host name resolves to must be
  global (no localhost / LAN / link-local / CGNAT / multicast …), checked again for every redirect hop and
  once more against the address actually connected to (DNS rebinding) — the media-proxy checks of
  ``playground_api`` are reused;
* a byte limit on the body (``Content-Length`` and the real stream), a limit on gunzipped data, timeouts,
  at most five redirects;
* the generic source accepts only hosts on the module's allow list (manifest ``net_import.hosts``, changed by
  the person in Settings -> Modules -> ⋯ -> «Import sources»); a module can never edit that list itself;
* the host window asks the person before the first download from each source («once» / «always»), the
  answer «always» is kept per module in ``state.json`` (``net_import.trusted``);
* the module receives the downloaded file (base64), nothing else — no headers, cookies or redirects.
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import io
import ipaddress
import json
import re
import socket
import urllib.parse
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import Request

SOURCES: Dict[str, Dict[str, Any]] = {
    "chub": {"label": "Chub (chub.ai)", "kind": "character", "hosts": ["api.chub.ai", "avatars.charhub.io"]},
    "chub-lorebook": {"label": "Chub lorebook (chub.ai)", "kind": "lorebook", "hosts": ["api.chub.ai"]},
    "janitor": {"label": "JanitorAI (jannyai.com)", "kind": "character", "hosts": ["api.jannyai.com"]},
    "pygmalion": {"label": "Pygmalion.chat", "kind": "character", "hosts": ["server.pygmalion.chat"]},
    "aicc": {"label": "AICharacterCards.com", "kind": "character", "hosts": ["aicharactercards.com"]},
    "risu": {"label": "RisuRealm (realm.risuai.net)", "kind": "character", "hosts": ["realm.risuai.net"]},
    "perchance": {"label": "Perchance (perchance.org)", "kind": "character", "hosts": ["user.uploads.dev"]},
    "generic": {"label": "Direct link", "kind": "character", "hosts": []},
}

DEFAULT_HOSTS: Tuple[str, ...] = ("files.catbox.moe", "cdn.discordapp.com", "raw.githubusercontent.com")
HOSTS_LIMIT = 64
CARD_LIMIT = 40 * 1024 * 1024
JSON_LIMIT = 20 * 1024 * 1024
AVATAR_LIMIT = 20 * 1024 * 1024
GZIP_LIMIT = 30 * 1024 * 1024
MAX_REDIRECTS = 5
TOTAL_TIMEOUT = 90.0
USER_AGENT = "MemuAI/3.1 (character import; +https://github.com/SillyTavern/SillyTavern compatible)"

_UUID_RE = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", re.I)
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.~-]{0,127}$")
_PERCHANCE_RE = re.compile(r"^\w+~([a-f0-9]{32}\.gz)$")
_RISU_RE = re.compile(r"^/character/([a-f0-9-]{8,80})/?$", re.I)
_HOST_RE = re.compile(r"^(\*\.)?(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?:\.(?!-)[a-z0-9-]{1,63})+$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

class ImportError_(Exception):
    """A failure the person should read: ``code`` for the module, a bilingual message for people."""

    def __init__(self, code: str, ru: str, en: str, status: int = 400, **extra: Any) -> None:
        super().__init__(f"{ru} / {en}")
        self.code, self.ru, self.en, self.status, self.extra = code, ru, en, status, extra

    def payload(self) -> Dict[str, Any]:
        return {"error": f"{self.ru} / {self.en}", "code": self.code, "message_ru": self.ru,
                "message_en": self.en, **self.extra}

def clean_host(raw: Any) -> str:
    """``files.catbox.moe`` / ``*.example.org``; IP literals, localhost and single labels are refused."""
    text = str(raw or "").strip().lower().rstrip(".")
    if "://" in text:
        text = urllib.parse.urlsplit(text).hostname or ""
    text = text.split("/", 1)[0]
    if not text or not _HOST_RE.match(text):
        return ""
    bare = text[2:] if text.startswith("*.") else text
    try:
        ipaddress.ip_address(bare)
        return ""
    except ValueError:
        pass
    if bare in ("localhost",) or bare.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
        return ""
    return text

def clean_hosts(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    for value in values or []:
        host = clean_host(value)
        if host and host not in out:
            out.append(host)
    return out[:HOSTS_LIMIT]

def normalize_manifest_block(raw: Any) -> Optional[Dict[str, Any]]:
    """The manifest's ``net_import`` block: ``{"sources": [...], "hosts": [...]}`` (None when absent)."""
    if not isinstance(raw, dict):
        return None
    sources = raw.get("sources")
    clean_sources = [s for s in dict.fromkeys(str(x) for x in sources) if s in SOURCES] \
        if isinstance(sources, list) else list(SOURCES)
    hosts = raw.get("hosts")
    return {"sources": clean_sources,
            "hosts": clean_hosts(hosts) if isinstance(hosts, list) else list(DEFAULT_HOSTS)}

def host_allowed(host: str, allowed: Iterable[str]) -> bool:
    host = str(host or "").lower().rstrip(".")
    for rule in allowed:
        if rule.startswith("*."):
            if host.endswith(rule[1:]) and host != rule[2:]:
                return True
            if host == rule[2:]:
                return True
        elif host == rule:
            return True
    return False

def _root():
    from .modules_host import default_root
    return default_root()

def module_settings(module_id: str, root=None) -> Dict[str, Any]:
    """What the module may import and what the person decided (effective hosts, trusted sources)."""
    from .modules_host import _overlay, load_package, load_state
    root = root or _root()
    package = load_package(root, module_id)
    block = normalize_manifest_block(package.get("net_import")) or normalize_manifest_block({})
    overlay = _overlay(load_state(root), package["id"]).get("net_import")
    overlay = overlay if isinstance(overlay, dict) else {}
    custom = overlay.get("hosts")
    hosts = clean_hosts(custom) if isinstance(custom, list) else list(block["hosts"])
    trusted = [s for s in (overlay.get("trusted") or []) if s in block["sources"]] \
        if isinstance(overlay.get("trusted"), list) else []
    return {"id": package["id"], "granted": "net-import" in (package.get("permissions") or []),
            "sources": block["sources"], "hosts": hosts, "default_hosts": list(block["hosts"]),
            "custom_hosts": isinstance(custom, list), "trusted": trusted,
            "labels": {key: SOURCES[key]["label"] for key in block["sources"]}}

def update_settings(module_id: str, *, hosts: Any = None, reset_hosts: bool = False,
                    trust: Any = None, untrust: Any = None, trusted: Any = None, root=None) -> Dict[str, Any]:
    """Change the person's choices. Called by the MemuAI window only (never through the module bridge)."""
    from .modules_host import _mutate_state, module_exists
    root = root or _root()
    if not module_exists(root, module_id):
        raise KeyError(module_id)

    def change(row: Dict[str, Any]) -> None:
        block = dict(row.get("net_import")) if isinstance(row.get("net_import"), dict) else {}
        if reset_hosts:
            block.pop("hosts", None)
        elif isinstance(hosts, list):
            block["hosts"] = clean_hosts(hosts)
        current = [s for s in (block.get("trusted") or []) if s in SOURCES]
        if isinstance(trusted, list):
            current = [s for s in dict.fromkeys(str(x) for x in trusted) if s in SOURCES]
        if trust and str(trust) in SOURCES and str(trust) not in current:
            current.append(str(trust))
        if untrust:
            current = [s for s in current if s != str(untrust)]
        block["trusted"] = current
        row["net_import"] = block

    _mutate_state(root, module_id, change)
    return module_settings(module_id, root)

def _chub_parts(parts: List[str]) -> Optional[Dict[str, str]]:
    """SillyTavern parseChubUrl after the domain: ``characters/a/b``, ``lorebooks/a/b`` or ``a/b``."""
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return None
    first = parts[0].lower()
    if first in ("characters", "lorebooks"):
        rest = parts[1:3]
        if len(rest) != 2 or not all(_SEGMENT_RE.match(p) for p in rest):
            return None
        if first == "characters":
            return {"source": "chub", "id": "/".join(rest)}
        return {"source": "chub-lorebook", "id": "lorebooks/" + "/".join(rest)}
    if len(parts) == 2 and all(_SEGMENT_RE.match(p) for p in parts):
        return {"source": "chub", "id": "/".join(parts)}
    return None

def parse_input(text: Any, hosts: Iterable[str] = DEFAULT_HOSTS) -> Dict[str, Any]:
    """A link or an id -> ``{source, id, url?, kind, label, hosts}``. No network here."""
    raw = str(text or "").strip()
    if not raw:
        raise ImportError_("empty", "Вставьте ссылку или идентификатор", "Paste a link or an id")
    if len(raw) > 2048:
        raise ImportError_("bad_input", "Слишком длинная ссылка", "The link is too long")
    found: Optional[Dict[str, str]] = None
    if re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I):
        parsed = urllib.parse.urlsplit(raw)
        host = (parsed.hostname or "").lower()
        if parsed.scheme.lower() not in ("http", "https") or not host:
            raise ImportError_("bad_input", "Поддерживаются только ссылки https://", "Only https:// links are supported")
        if parsed.username is not None or parsed.password is not None:
            raise ImportError_("bad_input", "Логин и пароль в ссылке запрещены", "Credentials in the link are not allowed")
        path = urllib.parse.unquote(parsed.path or "")
        if "pygmalion.chat" in host:
            uuid = _UUID_RE.search(raw)
            found = {"source": "pygmalion", "id": uuid.group(0).lower()} if uuid else None
        elif "janitorai" in host or host.endswith("jannyai.com"):
            uuid = _UUID_RE.search(raw)
            found = {"source": "janitor", "id": uuid.group(0).lower()} if uuid else None
        elif host.endswith("aicharactercards.com"):
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 2 and all(_SEGMENT_RE.match(p) for p in parts[-2:]):
                found = {"source": "aicc", "id": f"{parts[-2]}/{parts[-1]}"}
        elif host in ("chub.ai", "characterhub.org") or host.endswith((".chub.ai", ".characterhub.org")):
            found = _chub_parts(path.split("/"))
        elif host == "realm.risuai.net":
            match = _RISU_RE.match(path)
            found = {"source": "risu", "id": match.group(1).lower()} if match else None
        elif host.endswith("perchance.org"):
            data = (urllib.parse.parse_qs(parsed.query).get("data") or [""])[0]
            match = _PERCHANCE_RE.match(data)
            found = {"source": "perchance", "id": match.group(1).lower()} if match else None
        elif host_allowed(host, hosts):
            if parsed.scheme.lower() != "https":
                raise ImportError_("bad_input", "Поддерживаются только ссылки https://", "Only https:// links are supported")
            found = {"source": "generic", "id": raw, "url": raw, "host": host}
        else:
            raise ImportError_("host_not_allowed",
                               f"Сайт {host} не в списке разрешённых для импорта. Добавьте его в «Настройки → Модули → ⋯ → Источники импорта»",
                               f"{host} is not on the import allow list. Add it in Settings → Modules → ⋯ → Import sources",
                               host=host)
        if not found:
            raise ImportError_("bad_input", "Не удалось распознать ссылку", "This link is not recognised")
    else:
        token = raw.strip("/")
        perchance = _PERCHANCE_RE.match(token)
        if "_character" in token and _UUID_RE.match(token):
            found = {"source": "janitor", "id": token.split("_", 1)[0].lower()}
        elif len(token) == 36 and _UUID_RE.fullmatch(token):
            found = {"source": "pygmalion", "id": token.lower()}
        elif token.upper().startswith("AICC/"):
            parts = [p for p in token.split("/")[1:] if p]
            if len(parts) == 2 and all(_SEGMENT_RE.match(p) for p in parts):
                found = {"source": "aicc", "id": "/".join(parts)}
        elif perchance:
            found = {"source": "perchance", "id": perchance.group(1).lower()}
        else:
            found = _chub_parts(token.split("/"))
        if not found:
            raise ImportError_("bad_input", "Не удалось распознать идентификатор",
                               "This id is not recognised")
    info = SOURCES[found["source"]]
    hosts_used = list(info["hosts"]) or [found.get("host", "")]
    return {**found, "kind": info["kind"], "label": info["label"], "hosts": hosts_used}

def check_public_https(url: str) -> str:
    """Only a public HTTPS address on the default port; the media-proxy DNS check does the rest."""
    parsed = urllib.parse.urlsplit(str(url or ""))
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ImportError_("not_https", "Разрешены только адреса https://", "Only https:// addresses are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise ImportError_("bad_input", "Логин и пароль в адресе запрещены", "Credentials in the address are not allowed")
    try:
        port = parsed.port
    except ValueError:
        port = -1
    if port not in (None, 443):
        raise ImportError_("bad_port", "Разрешён только стандартный порт HTTPS", "Only the standard HTTPS port is allowed")

    try:
        answers = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ImportError_("dns", "Не удалось найти сайт", "The site could not be found", 502) from exc
    for answer in answers:
        address = ipaddress.ip_address(str(answer).split("%", 1)[0])
        if not address.is_global or address.is_multicast or address.is_reserved or address.is_unspecified:
            raise ImportError_("private_address", "Локальные и приватные адреса запрещены",
                               "Local and private addresses are not allowed")

    from .playground_api import _safe_media_url
    try:
        checked = _safe_media_url(url)
    except ValueError as exc:
        raise ImportError_("private_address", "Локальные и приватные адреса запрещены",
                           "Local and private addresses are not allowed") from exc
    return checked

def _peer_is_public(response) -> None:
    from .playground_api import _require_public_response_peer
    try:
        _require_public_response_peer(response)
    except ValueError as exc:
        raise ImportError_("private_address", "Сервер оказался локальным адресом",
                           "The server resolved to a local address") from exc

def _looks_blocked(status: int, headers, body: bytes) -> bool:
    head = body[:4000].lower()
    return (status in (403, 429, 503) and (b"cloudflare" in head or b"just a moment" in head
                                           or b"cf-chl" in head or headers.get("cf-mitigated") is not None))

async def safe_fetch(client, url: str, *, method: str = "GET", json_body: Any = None,
                     headers: Optional[Dict[str, str]] = None, limit: int = CARD_LIMIT,
                     label: str = "") -> Tuple[bytes, str, str]:
    """Download one resource: every hop public HTTPS, limited size. Returns (body, content-type, final url)."""
    current = await asyncio.to_thread(check_public_https, url)
    send_headers = {"User-Agent": USER_AGENT, "Accept": "*/*", **(headers or {})}
    for _ in range(MAX_REDIRECTS + 1):
        kwargs: Dict[str, Any] = {"headers": send_headers, "follow_redirects": False}
        if json_body is not None and method == "POST":
            kwargs["json"] = json_body
        async with client.stream(method, current, **kwargs) as response:
            _peer_is_public(response)
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location", "")
                if not location:
                    raise ImportError_("http", "Сайт вернул пустое перенаправление", "The site returned an empty redirect", 502)
                current = await asyncio.to_thread(check_public_https, urllib.parse.urljoin(current, location))
                if response.status_code == 303 or (response.status_code in (301, 302) and method == "POST"):
                    method, json_body = "GET", None
                continue
            from .playground_api import _read_limited_response
            try:
                body = await _read_limited_response(response, limit)
            except OverflowError as exc:
                raise ImportError_("too_large", "Файл слишком большой", "The file is too large", 413) from exc
            status = response.status_code
            if status == 404:
                raise ImportError_("not_found", f"{label or 'Сайт'}: не найдено (404)",
                                   f"{label or 'The site'}: not found (404)", 404)
            if _looks_blocked(status, response.headers, body):
                raise ImportError_("blocked",
                                   f"{label or 'Сайт'} не пускает автоматическую загрузку (защита Cloudflare). Скачайте карточку в браузере и перетащите файл сюда",
                                   f"{label or 'The site'} blocks automated downloads (Cloudflare protection). Download the card in a browser and drop the file here",
                                   502)
            if status < 200 or status >= 300:
                raise ImportError_("http", f"{label or 'Сайт'} ответил ошибкой {status}",
                                   f"{label or 'The site'} answered with error {status}", 502, http_status=status)
            return body, response.headers.get("content-type", "").split(";", 1)[0].strip().lower(), current
    raise ImportError_("redirects", "Слишком много перенаправлений", "Too many redirects", 502)

def _json_of(body: bytes, label: str) -> Any:
    try:
        return json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        if body[:200].lstrip().lower().startswith((b"<!doctype", b"<html")):
            raise ImportError_("blocked", f"{label} вернул веб-страницу вместо данных (возможно, защита от ботов)",
                               f"{label} returned a web page instead of data (bot protection?)", 502) from exc
        raise ImportError_("bad_data", f"{label} вернул неожиданные данные", f"{label} returned unexpected data", 502) from exc

def _file(name: str, mime: str, data: bytes) -> Dict[str, Any]:
    return {"name": name, "mime": mime, "bytes": len(data), "base64": base64.b64encode(data).decode("ascii")}

def _safe_name(text: Any, ext: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", str(text or "")).strip(" .")[:100] or "import"
    return name + ext

def _kind_of(body: bytes, content_type: str) -> str:
    if body.startswith(PNG_SIGNATURE):
        return "png"
    if body[:1] in (b"{", b"[") or content_type in ("application/json", "text/json") or body[:3] == b"\xef\xbb\xbf":
        return "json"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "webp"
    return ""

async def _avatar(client, url: Any) -> Optional[Dict[str, Any]]:
    """A best-effort avatar (public HTTPS image or a data: URL). Failures only mean «no picture»."""
    text = str(url or "").strip()
    if text.startswith("data:image/"):
        header, _, payload = text.partition(",")
        try:
            data = base64.b64decode(payload, validate=False) if ";base64" in header else urllib.parse.unquote_to_bytes(payload)
        except (ValueError, TypeError):
            return None
        if 0 < len(data) <= AVATAR_LIMIT:
            return _file("avatar", header[5:].split(";", 1)[0], data)
        return None
    if not text.lower().startswith("https://"):
        return None
    try:
        body, content_type, _ = await safe_fetch(client, text, limit=AVATAR_LIMIT, label="avatar",
                                                 headers={"Accept": "image/*"})
    except ImportError_:
        return None
    if not (content_type.startswith("image/") or body.startswith(PNG_SIGNATURE)):
        return None
    return _file("avatar", content_type if content_type.startswith("image/") else "image/png", body)

async def _chub_character(client, ident: str) -> Dict[str, Any]:
    creator, project = ident.split("/", 1)
    url = "https://api.chub.ai/api/characters/%s/%s?full=true" % (urllib.parse.quote(creator, safe=""),
                                                                    urllib.parse.quote(project, safe=""))
    body, _, _ = await safe_fetch(client, url, headers={"Accept": "application/json"}, limit=JSON_LIMIT, label="Chub")
    meta = _json_of(body, "Chub")
    node = meta.get("node") if isinstance(meta, dict) else None
    definition = node.get("definition") if isinstance(node, dict) else None
    if not isinstance(definition, dict):
        raise ImportError_("not_found", "Chub: персонаж не найден", "Chub: character not found", 404)
    extensions = definition.get("extensions") if isinstance(definition.get("extensions"), dict) else {}
    extensions = {**extensions}
    extensions.setdefault("chub", {"id": node.get("id"), "full_path": node.get("fullPath") or ident})
    card = {"spec": "chara_card_v2", "spec_version": "2.0", "data": {

        "name": definition.get("name") or node.get("name") or project,
        "description": definition.get("personality") or "",
        "personality": definition.get("tavern_personality") or "",
        "scenario": definition.get("scenario") or "",
        "first_mes": definition.get("first_message") or "",
        "mes_example": definition.get("example_dialogs") or "",
        "creator_notes": definition.get("description") or "",
        "system_prompt": definition.get("system_prompt") or "",
        "post_history_instructions": definition.get("post_history_instructions") or "",
        "alternate_greetings": definition.get("alternate_greetings") or [],
        "tags": node.get("topics") or [],
        "creator": creator,
        "character_version": "",
        "extensions": extensions,
    }}
    if isinstance(definition.get("embedded_lorebook"), dict):
        card["data"]["character_book"] = definition["embedded_lorebook"]
    avatar = await _avatar(client, node.get("max_res_url") or node.get("avatar_url"))
    name = card["data"]["name"]
    return {"type": "character", "name": name, "file": _file(_safe_name(name, ".json"), "application/json",
                                                            json.dumps(card, ensure_ascii=False).encode("utf-8")),
            "avatar": avatar, "page": f"https://chub.ai/characters/{ident}"}

async def _chub_lorebook(client, ident: str) -> Dict[str, Any]:
    _, creator, project = ident.split("/", 2)
    url = "https://api.chub.ai/api/lorebooks/%s/%s" % (urllib.parse.quote(creator, safe=""),
                                                     urllib.parse.quote(project, safe=""))
    body, _, _ = await safe_fetch(client, url, headers={"Accept": "application/json"}, limit=JSON_LIMIT, label="Chub")
    meta = _json_of(body, "Chub")
    node = meta.get("node") if isinstance(meta, dict) else None
    project_id = node.get("id") if isinstance(node, dict) else None
    if not isinstance(project_id, int):
        raise ImportError_("not_found", "Chub: лорбук не найден", "Chub: lorebook not found", 404)
    raw = f"https://api.chub.ai/api/v4/projects/{project_id}/repository/files/raw%252Fsillytavern_raw.json/raw"
    data, _, _ = await safe_fetch(client, raw, headers={"Accept": "application/json"}, limit=JSON_LIMIT, label="Chub")
    _json_of(data, "Chub")
    name = node.get("name") or project
    return {"type": "lorebook", "name": name, "file": _file(_safe_name(project, ".json"), "application/json", data),
            "avatar": None, "page": f"https://chub.ai/{ident}"}

async def _janitor(client, ident: str) -> Dict[str, Any]:

    body, _, _ = await safe_fetch(client, "https://api.jannyai.com/api/v1/download", method="POST",
                                  json_body={"characterId": ident},
                                  headers={"Content-Type": "application/json", "Accept": "application/json"},
                                  limit=JSON_LIMIT, label="JanitorAI")
    result = _json_of(body, "JanitorAI")
    if not isinstance(result, dict) or result.get("status") != "ok" or not result.get("downloadUrl"):
        raise ImportError_("not_found", "JanitorAI: персонаж не найден или скрыт автором",
                           "JanitorAI: the character is missing or hidden by its author", 404)
    data, content_type, _ = await safe_fetch(client, str(result["downloadUrl"]), limit=CARD_LIMIT, label="JanitorAI")
    return _card_file(data, content_type, ident, "JanitorAI", f"https://janitorai.com/characters/{ident}")

async def _pygmalion(client, ident: str) -> Dict[str, Any]:
    body, _, _ = await safe_fetch(client, f"https://server.pygmalion.chat/api/export/character/{ident}/v2",
                                  headers={"Accept": "application/json"}, limit=JSON_LIMIT, label="Pygmalion")
    result = _json_of(body, "Pygmalion")
    character = result.get("character") if isinstance(result, dict) else None
    if not isinstance(character, dict):
        raise ImportError_("not_found", "Pygmalion: персонаж не найден", "Pygmalion: character not found", 404)
    data = character.get("data") if isinstance(character.get("data"), dict) else {}
    avatar = await _avatar(client, data.get("avatar"))
    name = data.get("name") or ident
    return {"type": "character", "name": name,
            "file": _file(_safe_name(name, ".json"), "application/json",
                          json.dumps(character, ensure_ascii=False).encode("utf-8")),
            "avatar": avatar, "page": f"https://pygmalion.chat/character/{ident}"}

async def _aicc(client, ident: str) -> Dict[str, Any]:
    author, card = ident.split("/", 1)
    url = "https://aicharactercards.com/wp-json/pngapi/v1/image/%s/%s" % (urllib.parse.quote(author, safe=""),
                                                                         urllib.parse.quote(card, safe=""))
    data, content_type, _ = await safe_fetch(client, url, limit=CARD_LIMIT, label="AICharacterCards")
    return _card_file(data, content_type, card, "AICharacterCards", f"https://aicharactercards.com/{ident}")

async def _risu(client, ident: str) -> Dict[str, Any]:
    url = f"https://realm.risuai.net/api/v1/download/png-v3/{urllib.parse.quote(ident, safe='')}?non_commercial=true"
    data, content_type, _ = await safe_fetch(client, url, limit=CARD_LIMIT, label="RisuRealm")
    return _card_file(data, content_type, ident, "RisuRealm", f"https://realm.risuai.net/character/{ident}")

async def _perchance(client, ident: str) -> Dict[str, Any]:
    url = f"https://user.uploads.dev/file/{urllib.parse.quote(ident, safe='.')}"
    packed, _, _ = await safe_fetch(client, url, limit=JSON_LIMIT, label="Perchance")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream:
            raw = stream.read(GZIP_LIMIT + 1)
    except (OSError, EOFError) as exc:
        raise ImportError_("bad_data", "Perchance: файл повреждён", "Perchance: the file is damaged", 502) from exc
    if len(raw) > GZIP_LIMIT:
        raise ImportError_("too_large", "Perchance: файл слишком большой", "Perchance: the file is too large", 413)
    payload = _json_of(raw, "Perchance")
    char = payload.get("addCharacter") if isinstance(payload, dict) else None
    if not isinstance(char, dict):
        raise ImportError_("bad_data", "Perchance: в файле нет персонажа", "Perchance: the file has no character", 502)
    avatar_url = str((char.get("avatar") or {}).get("url") or "") if isinstance(char.get("avatar"), dict) else ""
    data = {
        "name": char.get("name") or "Unnamed Perchance Character", "first_mes": "", "tags": [],
        "description": char.get("roleInstruction") or "", "creator": char.get("metaTitle") or "",
        "creator_notes": char.get("metaDescription") or "", "alternate_greetings": [], "character_version": "",
        "mes_example": "", "post_history_instructions": "", "system_prompt": "", "scenario": "",
        "personality": char.get("reminderMessage") or "",
        "extensions": {"perchance_data": {
            "slug": ident, "char_url": url, "uuid": char.get("uuid"),
            "avatar_url": None if avatar_url.startswith("data:") else (avatar_url or None),
            "folder_path": char.get("folderPath"), "folder_name": char.get("folderName"),
            "custom_data": char.get("customData") or {}}},
    }
    if isinstance(char.get("initialMessages"), list):
        first = next((m for m in char["initialMessages"] if isinstance(m, dict) and m.get("author") == "ai"), None)
        if first and first.get("content"):
            data["first_mes"] = str(first["content"])
    card = {"spec": "chara_card_v2", "spec_version": "2.0", "data": data}
    return {"type": "character", "name": data["name"],
            "file": _file(_safe_name(data["name"], ".json"), "application/json",
                          json.dumps(card, ensure_ascii=False).encode("utf-8")),
            "avatar": await _avatar(client, avatar_url), "page": f"https://perchance.org/ai-character-chat?data={ident}"}

async def _generic(client, url: str) -> Dict[str, Any]:
    data, content_type, final = await safe_fetch(client, url, limit=CARD_LIMIT, label=urllib.parse.urlsplit(url).hostname or "")
    name = urllib.parse.unquote(urllib.parse.urlsplit(final).path.rsplit("/", 1)[-1]) or "card"
    return _card_file(data, content_type, re.sub(r"\.(png|json)$", "", name, flags=re.I), name, url, any_json=True)

def _card_file(data: bytes, content_type: str, name: str, label: str, page: str, *, any_json: bool = False) -> Dict[str, Any]:
    kind = _kind_of(data, content_type)
    if kind == "png":
        return {"type": "character", "name": name, "file": _file(_safe_name(name, ".png"), "image/png", data),
                "avatar": None, "page": page}
    if kind == "json":
        parsed = _json_of(data, label)
        detected = "lorebook" if isinstance(parsed, dict) and ("entries" in parsed or parsed.get("kind") == "memory"
                                                                or parsed.get("type") == "risu"
                                                                or parsed.get("lorebookVersion") is not None) \
            and not parsed.get("spec") else "character"
        if not any_json and detected != "character":
            detected = "character"
        return {"type": detected, "name": name, "file": _file(_safe_name(name, ".json"), "application/json", data),
                "avatar": None, "page": page}
    if data[:200].lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise ImportError_("blocked", f"{label} вернул веб-страницу вместо карточки (возможно, защита от ботов)",
                           f"{label} returned a web page instead of a card (bot protection?)", 502)
    raise ImportError_("bad_data", f"{label}: это не PNG-карточка и не JSON",
                       f"{label}: this is neither a PNG card nor JSON", 415)

RESOLVERS = {"chub": _chub_character, "chub-lorebook": _chub_lorebook, "janitor": _janitor,
             "pygmalion": _pygmalion, "aicc": _aicc, "risu": _risu, "perchance": _perchance}

def make_client():
    import httpx

    return httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), trust_env=False, http2=False)

async def fetch_import(target: Dict[str, Any], client=None) -> Dict[str, Any]:
    """Download what ``parse_input`` resolved. Returns the payload for the module."""
    own = client is None
    client = client or make_client()
    try:
        source = target["source"]
        if source == "generic":
            coroutine = _generic(client, target["url"])
        else:
            coroutine = RESOLVERS[source](client, target["id"])
        try:
            result = await asyncio.wait_for(coroutine, TOTAL_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise ImportError_("timeout", "Сайт не ответил вовремя", "The site did not answer in time", 504) from exc
        except ImportError_:
            raise
        except Exception as exc:
            name = type(exc).__name__
            raise ImportError_("network", f"Не удалось связаться с сайтом ({name})",
                               f"Could not reach the site ({name})", 502) from exc
        return {"ok": True, "source": source, "label": SOURCES[source]["label"], "id": target.get("id"), **result}
    finally:
        if own:
            await client.aclose()

def register(app, *, allowed) -> None:
    """``POST /api/pg/modules/net-import`` (module bridge ``Memu.fetchImport``, action ``net_import`` ->
    permission ``net-import``) and ``/api/pg/modules/net-import/settings`` (MemuAI window only)."""
    from fastapi.responses import JSONResponse

    async def body_of(request: Request) -> Dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            raise ImportError_("bad_input", "Некорректный JSON", "Invalid JSON")
        if not isinstance(body, dict):
            raise ImportError_("bad_input", "Ожидается JSON-объект", "A JSON object is expected")
        return body

    @app.post("/api/pg/modules/net-import")
    async def modules_net_import(request: Request):
        try:
            body = await body_of(request)
            from .characters_api import _clean_id
            module_id = _clean_id(body.get("module_id"))
            if not allowed(module_id, "net_import"):
                return JSONResponse({"error": "Модулю не выдано право «net-import» / The module lacks the “net-import” permission",
                                     "code": "permission"}, 403)
            settings = await asyncio.to_thread(module_settings, module_id)
            target = parse_input(body.get("input"), settings["hosts"])
            kind = str(body.get("kind") or "auto")
            if kind in ("character", "lorebook") and target["kind"] != kind and target["source"] != "generic":
                raise ImportError_("wrong_kind",
                                   "Эта ссылка ведёт на лорбук" if target["kind"] == "lorebook" else "Эта ссылка ведёт на персонажа",
                                   "This link points to a lorebook" if target["kind"] == "lorebook" else "This link points to a character")
            if target["source"] not in settings["sources"]:
                raise ImportError_("source_not_allowed", "Модуль не объявил этот источник",
                                   "The module did not declare this source", 403)
            trusted = target["source"] in settings["trusted"]
            if str(body.get("op") or "fetch") == "resolve":
                return {"ok": True, **target, "trusted": trusted}

            if not trusted and body.get("approved") is not True:
                raise ImportError_("needs_approval", "Нужно разрешение на загрузку", "Permission to download is required", 403)
            return await fetch_import(target)
        except ImportError_ as exc:
            return JSONResponse(exc.payload(), exc.status)
        except KeyError:
            return JSONResponse({"error": "Модуль не найден / Module not found", "code": "module"}, 404)

    @app.post("/api/pg/modules/net-import/settings")
    async def modules_net_import_settings(request: Request):
        try:
            body = await body_of(request)
            from .characters_api import _clean_id
            module_id = _clean_id(body.get("id") or body.get("module_id"))
            if any(key in body for key in ("hosts", "reset_hosts", "trust", "untrust", "trusted")):
                result = await asyncio.to_thread(
                    update_settings, module_id, hosts=body.get("hosts"), reset_hosts=bool(body.get("reset_hosts")),
                    trust=body.get("trust"), untrust=body.get("untrust"), trusted=body.get("trusted"))
            else:
                result = await asyncio.to_thread(module_settings, module_id)
            return {"ok": True, **result}
        except ImportError_ as exc:
            return JSONResponse(exc.payload(), exc.status)
        except KeyError:
            return JSONResponse({"error": "Модуль не найден / Module not found"}, 404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)

__all__ = ["register", "parse_input", "fetch_import", "safe_fetch", "check_public_https", "clean_host",
           "clean_hosts", "host_allowed", "module_settings", "update_settings", "normalize_manifest_block",
           "SOURCES", "DEFAULT_HOSTS", "ImportError_"]
