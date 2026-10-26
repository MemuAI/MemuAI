"""Round 35 (protocol): ``memuai://`` deep links — strict parser, route registry, pending queue.

A link never runs anything by itself.  The pipeline is::

    MemuAI.exe --deeplink "memuai://chat/new?q=hi"   (Windows protocol handler, HKCU\\Software\\Classes\\memuai)
    memu open memuai://…                              (the ``memu`` CLI)
    a click on a memuai:// link inside MemuAI
        → POST /api/pg/deeplink/dispatch {url, source}      (app/deeplinks_api.py)
        → :func:`dispatch`: settings, burst limit, :func:`parse`, route lookup, param validation
        → a *pending item* (random id, 10 min TTL) + a nudge to the window
        → the MemuAI window (pgstatic/deeplinks.js) shows it:
             ``safe``    – executed at once (navigation only: open a chat/tab/settings page, prefill a text)
             ``confirm`` – a dialog INSIDE MemuAI with exactly what will happen, the link and its source;
                           nothing happens until «Выполнить» (the button unlocks after a short delay)
             ``block``   – never reachable from a link
        → POST /api/pg/deeplink/item/{id}/run → the route handler (server part) → a UI action for the window.

Other zones add their own targets::

    from app import deeplinks
    deeplinks.register_route("myzone/open", handler=None, danger="safe",
                             params={"id": deeplinks.P("id", required=True)},
                             title=("Открыть …", "Open …"), ui="myzone.open")

``handler(params, ctx)`` (sync or async) returns ``{"ok": True, "ui": {"do": "<action>", ...}, "message": "…"}``;
without a handler the window receives ``{"do": ui, **params}`` and its ``MemuLinks.registerAction(name, fn)``
callback runs it.  Every state-changing or cost-incurring route MUST be ``danger="confirm"`` (or use
``confirm_if``).  Keys/tokens are never accepted in a link (``BLOCKED_PARAM_NAMES`` + secret-like values).
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import inspect
import ipaddress
import json
import logging
import os
import re
import secrets
import threading
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger("memuai.deeplinks")

SCHEME = "memuai"
MAX_URL = 8192
MAX_PARAMS = 16
MAX_SEGMENTS = 6
DEFAULT_VALUE_MAX = 1000
PENDING_TTL = 600.0
PENDING_MAX = 12
CONFIRM_MIN_SHOWN = 0.6
BURST = ((10.0, 6), (600.0, 60))
SOURCES = ("os", "cli", "chat", "app", "test")
DANGERS = ("safe", "confirm", "block")

BLOCKED_PARAM_NAMES = frozenset({
    "key", "api_key", "apikey", "api-key", "token", "access_token", "refresh_token", "id_token", "secret",
    "client_secret", "password", "passwd", "pass", "pwd", "auth", "authorization", "bearer", "cookie",
    "session", "session_id", "sessionid", "private_key", "otp", "pin", "code_verifier", "credentials",
})
_SECRET_VALUE = re.compile(
    r"(?:\bsk-(?:ant-|proj-|or-)?[A-Za-z0-9_\-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|\bxox[abprs]-[A-Za-z0-9\-]{10,}|\bAIza[0-9A-Za-z_\-]{30,}|\bya29\.[0-9A-Za-z_\-]{20,}|\bAKIA[0-9A-Z]{16}\b"
    r"|\bglpat-[A-Za-z0-9_\-]{16,}|\bhf_[A-Za-z0-9]{30,}|\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\."
    r"|\bBearer\s+[A-Za-z0-9._\-]{16,}|\b[0-9]{8,10}:AA[A-Za-z0-9_\-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")
_LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-+/=.]{40,}")
_BIDI = re.compile("[\u202a-\u202e\u2066-\u2069\u200e\u200f\u061c]")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_SEG_RE = re.compile(r"[A-Za-z0-9._~\-]{1,160}")
_ROUTE_RE = re.compile(r"[a-z0-9][a-z0-9\-]{0,31}(?:/[a-z0-9][a-z0-9\-]{0,31}){0,3}")
_WIN_DEVICE = re.compile(r"(?i)^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$")
_FORBIDDEN_SCHEMES = ("javascript:", "vbscript:", "file:", "data:", "about:", "blob:", "ms-", "shell:", "search-ms:")

class LinkError(ValueError):
    """A rejected link: ``code`` is stable (tests, CLI exit codes), ``ru``/``en`` are for people."""

    def __init__(self, code: str, ru: str, en: str = "") -> None:
        super().__init__(en or ru)
        self.code = code
        self.ru = ru
        self.en = en or ru

    def as_dict(self, lang: str = "ru") -> Dict[str, Any]:
        return {"ok": False, "error": self.code, "message": self.en if lang == "en" else self.ru}

@dataclass(frozen=True)
class P:
    """A parameter spec: kind ∈ text|line|id|slug|enum|bool|int|path|url|model|b64json."""
    kind: str = "line"
    required: bool = False
    max: int = 0
    choices: Tuple[str, ...] = ()
    default: Any = None
    hosts: Tuple[str, ...] = ()

    def limit(self) -> int:
        if self.max:
            return self.max
        return {"text": 4000, "line": 300, "id": 128, "slug": 64, "enum": 64, "bool": 5, "int": 12,
                "path": 1024, "url": 2048, "model": 200, "b64json": 6000}.get(self.kind, DEFAULT_VALUE_MAX)

def _looks_secret(value: str) -> bool:
    if _SECRET_VALUE.search(value):
        return True
    for token in _LONG_TOKEN.findall(value):

        if "/" in token and token.count("/") > 2:
            continue
        has_digit = any(c.isdigit() for c in token)
        has_upper = any(c.isupper() for c in token)
        has_lower = any(c.islower() for c in token)
        if has_digit and has_lower and (has_upper or len(token) >= 48):
            return True
    return False

def _clean_text(value: str, multiline: bool) -> str:
    value = unicodedata.normalize("NFC", value)
    if _BIDI.search(value):
        raise LinkError("bidi", "В ссылке есть невидимые символы смены направления текста — она может обманывать.",
                        "The link contains invisible text-direction characters — it may be deceptive.")
    if _CTRL.search(value) or (not multiline and ("\n" in value or "\r" in value or "\t" in value)):
        raise LinkError("control_chars", "В ссылке есть управляющие символы.", "The link contains control characters.")
    return value.replace("\r\n", "\n").replace("\r", "\n")

def check_local_path(value: str) -> str:
    """A local absolute path, or LinkError.  No UNC/network shares (they leak the Windows login hash),
    no device names, no ``..``, no ``\\\\?\\`` prefixes, no environment variables."""
    raw = str(value or "").strip().strip('"')
    if not raw:
        raise LinkError("bad_path", "Пустой путь.", "Empty path.")
    low = raw.lower()
    if any(low.startswith(s) for s in _FORBIDDEN_SCHEMES) or "://" in raw:
        raise LinkError("bad_path", "Это не путь к файлу.", "This is not a file path.")
    if raw.startswith(("\\\\", "//")) or raw.startswith(("\\??\\", "\\\\?\\", "\\\\.\\")):
        raise LinkError("unc_path", "Сетевые пути (\\\\сервер\\папка) по ссылкам не открываются.",
                        "Network paths (\\\\server\\share) are not opened from links.")
    if "%" in raw or raw.startswith("~") or "$" in raw:
        raise LinkError("bad_path", "Переменные окружения в пути не поддерживаются.", "Environment variables in paths are not supported.")
    windows = re.match(r"^[A-Za-z]:[\\/]", raw) is not None
    posix = raw.startswith("/") and os.name != "nt"
    if not (windows or posix):
        raise LinkError("bad_path", "Нужен полный путь, например C:\\Users\\Имя\\file.txt.",
                        "A full path is needed, e.g. C:\\Users\\Name\\file.txt.")
    parts = re.split(r"[\\/]+", raw[2:] if windows else raw)
    for part in parts:
        if part in ("..",):
            raise LinkError("bad_path", "Путь с «..» не принимается.", "Paths with \"..\" are not accepted.")
        if part and (_WIN_DEVICE.match(part.rstrip(". ")) or ":" in part):
            raise LinkError("bad_path", "Недопустимое имя в пути.", "Invalid name in the path.")
    return raw

def check_public_url(value: str, hosts: Sequence[str] = ()) -> str:
    raw = str(value or "").strip()
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError as exc:
        raise LinkError("bad_url", "Неверный адрес.", "Invalid address.") from exc
    if parts.scheme.lower() != "https":
        raise LinkError("bad_url", "Принимаются только адреса https://.", "Only https:// addresses are accepted.")
    if parts.username or parts.password or "@" in parts.netloc:
        raise LinkError("bad_url", "Адрес с логином внутри не принимается.", "Addresses with credentials are not accepted.")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or host in ("localhost",) or host.endswith((".local", ".internal", ".lan", ".localhost")):
        raise LinkError("bad_url", "Локальные адреса по ссылкам не открываются.", "Local addresses are not opened from links.")
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        ip = None
    if ip is not None and not ip.is_global:
        raise LinkError("bad_url", "Локальные адреса по ссылкам не открываются.", "Local addresses are not opened from links.")
    if hosts and host not in hosts:
        raise LinkError("bad_host", "Этот сайт не поддерживается: " + ", ".join(hosts) + ".",
                        "This site is not supported: " + ", ".join(hosts) + ".")
    return raw

def _validate_value(name: str, spec: P, value: str) -> Any:
    if len(value) > spec.limit():
        raise LinkError("too_long", f"Параметр «{name}» слишком длинный (больше {spec.limit()} символов).",
                        f"Parameter \"{name}\" is too long (over {spec.limit()} characters).")
    kind = spec.kind
    value = _clean_text(value, multiline=kind == "text")
    if kind not in ("b64json",) and _looks_secret(value):
        raise LinkError("secret_param", "Похоже, в ссылке ключ или токен. Ключи по ссылкам не передаются — "
                        "вводите их только в окне MemuAI.",
                        "The link seems to carry a key or token. Keys are never passed in links — type them in MemuAI only.")
    low = value.strip().lower()
    if kind in ("text", "line") and any(low.startswith(s) for s in ("javascript:", "vbscript:", "data:text/html")):
        raise LinkError("bad_value", "Недопустимое значение.", "Invalid value.")
    if kind in ("text", "line"):
        return value
    if kind == "id":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}", value):
            raise LinkError("bad_value", f"Неверный идентификатор в «{name}».", f"Invalid id in \"{name}\".")
        return value
    if kind == "slug":
        if not re.fullmatch(r"[a-z0-9][a-z0-9_\-]{0,63}", value.lower()):
            raise LinkError("bad_value", f"Неверное значение «{name}».", f"Invalid value of \"{name}\".")
        return value.lower()
    if kind == "enum":
        if value.lower() not in spec.choices:
            raise LinkError("bad_value", f"«{name}» может быть только: " + ", ".join(spec.choices) + ".",
                            f"\"{name}\" can only be: " + ", ".join(spec.choices) + ".")
        return value.lower()
    if kind == "bool":
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off", ""):
            return False
        raise LinkError("bad_value", f"«{name}» — 0 или 1.", f"\"{name}\" is 0 or 1.")
    if kind == "int":
        if not re.fullmatch(r"-?\d{1,9}", value):
            raise LinkError("bad_value", f"«{name}» — число.", f"\"{name}\" is a number.")
        return int(value)
    if kind == "model":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}", value):
            raise LinkError("bad_value", "Неверное имя модели.", "Invalid model name.")
        return value
    if kind == "path":
        return check_local_path(value)
    if kind == "url":
        return check_public_url(value, spec.hosts)
    if kind == "b64json":
        padded = value.strip() + "=" * (-len(value.strip()) % 4)
        try:
            data = base64.urlsafe_b64decode(padded.encode("ascii"))
            if len(data) > 32 * 1024:
                raise LinkError("too_long", "Данные в ссылке слишком большие.", "The data in the link is too large.")
            decoded = json.loads(data.decode("utf-8"))
        except LinkError:
            raise
        except (binascii.Error, ValueError, UnicodeError) as exc:
            raise LinkError("bad_value", f"«{name}» — не JSON в base64url.", f"\"{name}\" is not base64url JSON.") from exc
        if not isinstance(decoded, (dict, list)):
            raise LinkError("bad_value", "Ожидался JSON-объект.", "A JSON object was expected.")
        text = json.dumps(decoded, ensure_ascii=False)
        if _looks_secret(text) or _has_secret_keys(decoded):
            raise LinkError("secret_param", "В данных ссылки найден ключ или токен — такие ссылки не принимаются.",
                            "The link data contains a key or token — such links are not accepted.")
        if _BIDI.search(text):
            raise LinkError("bidi", "В данных есть невидимые символы направления текста.", "The data has invisible direction characters.")
        return decoded
    raise LinkError("bad_spec", "Неизвестный тип параметра.", "Unknown parameter type.")

def _has_secret_keys(value: Any, depth: int = 0) -> bool:
    if depth > 12:
        return True
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower().replace("-", "_") in BLOCKED_PARAM_NAMES and item not in (None, "", [], {}):
                return True
            if _has_secret_keys(item, depth + 1):
                return True
    elif isinstance(value, list):
        return any(_has_secret_keys(item, depth + 1) for item in value[:500])
    return False

@dataclass
class Route:
    name: str
    handler: Optional[Callable[..., Any]] = None
    danger: str = "safe"
    params: Dict[str, P] = field(default_factory=dict)
    positional: Tuple[str, ...] = ()
    title: Tuple[str, str] = ("", "")
    describe: Optional[Callable[[Dict[str, Any], str], List[str]]] = None
    confirm_if: Optional[Callable[[Dict[str, Any]], bool]] = None
    preview: Optional[Callable[..., Any]] = None
    inputs: Tuple[Dict[str, Any], ...] = ()
    ui: str = ""
    example: str = ""
    owner: str = ""
    cli_ok: bool = True

    def public(self, lang: str = "ru") -> Dict[str, Any]:
        return {"name": self.name, "danger": self.danger, "title": self.title[1 if lang == "en" else 0] or self.name,
                "params": {k: {"kind": v.kind, "required": v.required, "choices": list(v.choices)} for k, v in self.params.items()},
                "positional": list(self.positional), "example": self.example, "owner": self.owner,
                "confirm_sometimes": bool(self.confirm_if)}

_ROUTES: Dict[str, Route] = {}
_ALIASES: Dict[str, str] = {}
_LOCK = threading.RLock()

def register_route(name: str, handler: Optional[Callable[..., Any]] = None, danger: str = "safe", *,
                   params: Optional[Dict[str, P]] = None, positional: Sequence[str] = (),
                   title: Tuple[str, str] = ("", ""), describe=None, confirm_if=None, preview=None,
                   inputs: Sequence[Dict[str, Any]] = (), ui: str = "", example: str = "", owner: str = "",
                   aliases: Sequence[str] = (), cli_ok: bool = True) -> Route:
    """Add (or replace) a link target.  ``danger``: ``safe`` (navigation only), ``confirm`` (dialog in the
    MemuAI window first), ``block`` (refused).  See the module docstring."""
    name = str(name or "").strip().lower().strip("/")
    if not _ROUTE_RE.fullmatch(name):
        raise ValueError(f"bad deeplink route name: {name!r}")
    if danger not in DANGERS:
        raise ValueError(f"bad danger level: {danger!r}")
    params = dict(params or {})
    for key in params:
        if not _KEY_RE.fullmatch(key) or key in BLOCKED_PARAM_NAMES:
            raise ValueError(f"bad deeplink param name: {key!r}")
    for key in positional:
        if key not in params:
            raise ValueError(f"positional {key!r} has no spec")
    route = Route(name=name, handler=handler, danger=danger, params=params, positional=tuple(positional),
                  title=tuple(title) if title else ("", ""), describe=describe, confirm_if=confirm_if, preview=preview,
                  inputs=tuple(dict(x) for x in inputs), ui=ui, example=example, owner=owner, cli_ok=cli_ok)
    with _LOCK:
        _ROUTES[name] = route
        for alias in aliases:
            alias = str(alias).strip().lower().strip("/")
            if _ROUTE_RE.fullmatch(alias):
                _ALIASES[alias] = name
    return route

def unregister_route(name: str) -> None:
    with _LOCK:
        _ROUTES.pop(str(name).lower(), None)

def routes() -> List[Route]:
    _ensure_builtins()
    with _LOCK:
        return sorted(_ROUTES.values(), key=lambda r: r.name)

def get_route(name: str) -> Optional[Route]:
    _ensure_builtins()
    with _LOCK:
        name = str(name or "").lower()
        return _ROUTES.get(_ALIASES.get(name, name))

_BUILTINS_LOADED = False

def _ensure_builtins() -> None:
    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return
    _BUILTINS_LOADED = True
    try:
        from . import deeplinks_routes  # noqa: F401  (registers the built-in routes)
    except Exception:  # pragma: no cover - a broken builtin must not break other zones' routes
        log.exception("built-in deeplink routes are unavailable")

@dataclass
class Link:
    route: str
    params: Dict[str, Any]
    raw: str
    segments: Tuple[str, ...] = ()

    def display(self, limit: int = 400) -> str:
        return self.raw if len(self.raw) <= limit else self.raw[:limit - 1] + "…"

def split(url: str) -> Tuple[List[str], List[Tuple[str, str]]]:
    """Syntax only: ``memuai://a/b?x=1`` → (["a","b"], [("x","1")]).  Raises LinkError."""
    if not isinstance(url, str):
        raise LinkError("not_text", "Ссылка должна быть текстом.", "The link must be text.")
    if len(url) > MAX_URL:
        raise LinkError("too_long", f"Ссылка длиннее {MAX_URL} символов.", f"The link is longer than {MAX_URL} characters.")
    raw = url.strip()
    if _CTRL.search(raw) or any(c in raw for c in "\r\n\t\x00 "):

        raise LinkError("control_chars", "В ссылке пробелы или управляющие символы — она должна быть закодирована.",
                        "The link contains spaces or control characters — it must be percent-encoded.")
    if _BIDI.search(raw):
        raise LinkError("bidi", "В ссылке есть невидимые символы направления текста.", "The link has invisible direction characters.")
    match = re.match(r"^([A-Za-z][A-Za-z0-9+.\-]*):", raw)
    if not match or match.group(1).lower() != SCHEME:
        raise LinkError("bad_scheme", "Это не ссылка memuai://.", "This is not a memuai:// link.")
    rest = raw[match.end():]
    if rest.startswith("//"):
        rest = rest[2:]
    rest = rest.split("#", 1)[0]
    path, _, query = rest.partition("?")
    if "\\" in path or "@" in path or ":" in path:
        raise LinkError("bad_path", "Недопустимые символы в адресе ссылки.", "Invalid characters in the link path.")
    segments = [s for s in path.split("/")]
    while segments and segments[-1] == "":
        segments.pop()
    if not segments or any(s == "" for s in segments):
        raise LinkError("no_route", "В ссылке не указано, что открыть.", "The link does not say what to open.")
    if len(segments) > MAX_SEGMENTS:
        raise LinkError("bad_path", "Слишком длинный путь в ссылке.", "The link path is too long.")
    decoded: List[str] = []
    for seg in segments:
        try:
            value = urllib.parse.unquote(seg, errors="strict") if "%" in seg else seg
        except UnicodeDecodeError as exc:
            raise LinkError("bad_encoding", "Неверная кодировка в ссылке.", "Invalid encoding in the link.") from exc
        if not _SEG_RE.fullmatch(value) or value in (".", ".."):
            raise LinkError("bad_path", "Недопустимая часть адреса ссылки.", "Invalid part of the link path.")
        decoded.append(value)
    pairs: List[Tuple[str, str]] = []
    if query:
        items = [x for x in query.split("&") if x != ""]
        if len(items) > MAX_PARAMS:
            raise LinkError("too_many_params", "Слишком много параметров.", "Too many parameters.")
        for item in items:
            key, sep, value = item.partition("=")
            try:
                key = urllib.parse.unquote_plus(key, errors="strict")
                value = urllib.parse.unquote_plus(value, errors="strict")
            except UnicodeDecodeError as exc:
                raise LinkError("bad_encoding", "Неверная кодировка в ссылке.", "Invalid encoding in the link.") from exc
            pairs.append((key, value))
    return decoded, pairs

def parse(url: str) -> Link:
    """Full validation against the registry: route, known params only, kinds, limits, no secrets."""
    _ensure_builtins()
    segments, pairs = split(url)
    route: Optional[Route] = None
    rest: List[str] = []
    for cut in range(min(len(segments), 4), 0, -1):
        candidate = "/".join(segments[:cut]).lower()
        found = get_route(candidate)
        if found:
            route, rest = found, segments[cut:]
            break
    if route is None:
        raise LinkError("unknown_route", "MemuAI не знает такого действия: " + "/".join(segments)[:80],
                        "MemuAI does not know this action: " + "/".join(segments)[:80])
    if route.danger == "block":
        raise LinkError("blocked", "Это действие нельзя выполнить по ссылке — только вручную в MemuAI.",
                        "This action cannot be run from a link — only by hand in MemuAI.")
    raw_params: Dict[str, str] = {}
    if len(rest) > len(route.positional):
        raise LinkError("bad_path", "Лишние части в адресе ссылки.", "Extra parts in the link path.")
    for name, value in zip(route.positional, rest):
        raw_params[name] = value
    for key, value in pairs:
        low = key.lower()
        if low.replace("-", "_") in BLOCKED_PARAM_NAMES or any(w in low for w in ("token", "secret", "password", "apikey", "api_key")):
            raise LinkError("secret_param", "Ссылки не могут передавать ключи, токены и пароли — вводите их только в окне MemuAI.",
                            "Links cannot carry keys, tokens or passwords — type them in the MemuAI window only.")
        if not _KEY_RE.fullmatch(key):
            raise LinkError("bad_param", "Недопустимое имя параметра.", "Invalid parameter name.")
        if key in raw_params:
            raise LinkError("duplicate_param", f"Параметр «{key}» повторяется.", f"Parameter \"{key}\" is repeated.")
        if key not in route.params:
            raise LinkError("unknown_param", f"Неизвестный параметр «{key}».", f"Unknown parameter \"{key}\".")
        raw_params[key] = value
    params: Dict[str, Any] = {}
    for name, spec in route.params.items():
        if name in raw_params:
            params[name] = _validate_value(name, spec, raw_params[name])
        elif spec.required:
            raise LinkError("missing_param", f"В ссылке не хватает «{name}».", f"The link lacks \"{name}\".")
        elif spec.default is not None:
            params[name] = spec.default
    return Link(route=route.name, params=params, raw=url.strip(), segments=tuple(segments))

def build(route: str, params: Optional[Dict[str, Any]] = None, *, positional: bool = True) -> str:
    """The canonical link for a route (used by «Копировать ссылку», ``memu link``)."""
    found = get_route(route)
    path = [route.strip("/")]
    query: List[Tuple[str, str]] = []
    params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    if found and positional:
        for name in found.positional:
            if name in params and isinstance(params[name], str) and _SEG_RE.fullmatch(params[name]):
                path.append(urllib.parse.quote(params.pop(name), safe=""))
            else:
                break
    for key, value in params.items():
        if isinstance(value, bool):
            value = "1" if value else "0"
        elif isinstance(value, (dict, list)):
            value = base64.urlsafe_b64encode(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()).decode().rstrip("=")
        query.append((key, str(value)))
    link = f"{SCHEME}://" + "/".join(path)
    if query:
        link += "?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
    return link

def _memu_dir() -> Path:
    base = os.environ.get("MEMUAI_DATA_DIR") or os.environ.get("OMNIHUB_DATA_DIR")
    if not base:
        base = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "MemuAI")
    path = Path(base) / "memuai"
    path.mkdir(parents=True, exist_ok=True)
    return path

DEFAULT_SETTINGS = {"enabled": True, "always_ask": False, "cli": True, "chat_links": True}

def load_settings() -> Dict[str, Any]:
    try:
        data = json.loads((_memu_dir() / "deeplinks.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    return {k: bool(data.get(k, v)) for k, v in DEFAULT_SETTINGS.items()}

def save_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    current = load_settings()
    for key in DEFAULT_SETTINGS:
        if key in patch:
            current[key] = bool(patch[key])
    path = _memu_dir() / "deeplinks.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    log.info("deeplink settings changed: %s", current)
    return current

_JOURNAL_MAX = 60
_journal_lock = threading.Lock()

def journal(limit: int = 30) -> List[Dict[str, Any]]:
    try:
        rows = json.loads((_memu_dir() / "deeplinks_log.json").read_text(encoding="utf-8"))
        rows = rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        rows = []
    return rows[-limit:][::-1]

def _record(event: str, *, url: str = "", route: str = "", source: str = "", detail: str = "") -> None:
    """Applog line + a small persistent journal (Settings → «Ссылки и командная строка» shows it)."""
    shown = url if len(url) <= 300 else url[:299] + "…"
    log.info("deeplink %s: route=%s source=%s %s %s", event, route or "-", source or "-", detail, shown)
    row = {"ts": round(time.time(), 1), "event": event, "route": route, "source": source,
           "detail": str(detail)[:200], "url": shown}
    with _journal_lock:
        path = _memu_dir() / "deeplinks_log.json"
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
            rows = rows if isinstance(rows, list) else []
        except (OSError, ValueError):
            rows = []
        rows = (rows + [row])[-_JOURNAL_MAX:]
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass

def clear_journal() -> None:
    with _journal_lock:
        try:
            (_memu_dir() / "deeplinks_log.json").unlink()
        except OSError:
            pass

class _Burst:
    def __init__(self) -> None:
        self.times: List[float] = []
        self.lock = threading.Lock()
        self.last_warn = 0.0

    def allow(self, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        with self.lock:
            horizon = max(w for w, _ in BURST)
            self.times = [t for t in self.times if now - t < horizon]
            for window, limit in BURST:
                if sum(1 for t in self.times if now - t < window) >= limit:
                    return False
            self.times.append(now)
            return True

    def reset(self) -> None:
        with self.lock:
            self.times.clear()

BURST_LIMIT = _Burst()

@dataclass
class Pending:
    id: str
    link: Link
    route: Route
    source: str
    danger: str
    created: float
    shown_at: float = 0.0
    done: bool = False
    note: str = ""
    cache: Dict[str, Any] = field(default_factory=dict)

    def public(self, lang: str = "ru") -> Dict[str, Any]:
        route = self.route
        try:
            lines = list(route.describe(self.link.params, lang)) if route.describe else []
        except Exception:
            lines = []
        title = route.title[1 if lang == "en" else 0] or route.name
        return {"id": self.id, "route": route.name, "title": title, "danger": self.danger, "source": self.source,
                "source_label": source_label(self.source, lang), "url": self.link.display(), "lines": lines[:12],
                "params": _public_params(self.link.params), "created": self.created,
                "has_preview": route.preview is not None,
                "inputs": [_public_input(x, lang) for x in route.inputs], "note": self.note}

def _public_params(params: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, (dict, list)):
            out[key] = "<json>"
        else:
            out[key] = value
    return out

def _public_input(spec: Dict[str, Any], lang: str) -> Dict[str, Any]:
    return {"id": spec.get("id"), "type": spec.get("type", "text"), "optional": bool(spec.get("optional", True)),
            "label": spec.get("label_en" if lang == "en" else "label_ru") or spec.get("id"),
            "hint": spec.get("hint_en" if lang == "en" else "hint_ru") or ""}

def source_label(source: str, lang: str = "ru") -> str:
    labels = {
        "os": ("Ссылка из другой программы или браузера", "A link from another program or browser"),
        "cli": ("Команда memu в терминале", "The memu command in a terminal"),
        "chat": ("Ссылка в чате (её мог написать ИИ)", "A link in a chat (the AI may have written it)"),
        "app": ("Ссылка внутри MemuAI", "A link inside MemuAI"),
        "test": ("Тест", "Test"),
    }
    pair = labels.get(source, labels["os"])
    return pair[1] if lang == "en" else pair[0]

_PENDING: Dict[str, Pending] = {}
_notifier: Optional[Callable[[Pending], None]] = None
_QUEUE_VER = 0

def queue_version() -> int:
    return _QUEUE_VER

def set_notifier(fn: Optional[Callable[[Pending], None]]) -> None:
    """The window nudge (playground: evaluate_js + bring the window up)."""
    global _notifier
    _notifier = fn

def _prune(now: float) -> None:
    for key in [k for k, p in _PENDING.items() if p.done or now - p.created > PENDING_TTL]:
        _PENDING.pop(key, None)
    while len(_PENDING) > PENDING_MAX:
        oldest = min(_PENDING.values(), key=lambda p: p.created)
        _PENDING.pop(oldest.id, None)

def dispatch(url: str, source: str = "os", *, lang: str = "ru", notify: bool = True) -> Dict[str, Any]:
    """Validate a link and queue it for the window.  Never runs the action."""
    global _QUEUE_VER
    source = source if source in SOURCES else "os"
    shown = str(url or "")[:300]
    settings = load_settings()
    if not settings["enabled"]:
        _record("disabled", url=shown, source=source)
        return LinkError("disabled", "Ссылки memuai:// выключены в настройках MemuAI.",
                         "memuai:// links are turned off in MemuAI settings.").as_dict(lang)
    if source == "cli" and not settings["cli"]:
        _record("cli_disabled", url=shown, source=source)
        return LinkError("cli_disabled", "Команды memu выключены в настройках MemuAI.",
                         "memu commands are turned off in MemuAI settings.").as_dict(lang)
    if source == "chat" and not settings["chat_links"]:
        _record("chat_disabled", url=shown, source=source)
        return LinkError("chat_disabled", "Ссылки memuai:// в чатах выключены в настройках.",
                         "memuai:// links in chats are turned off in settings.").as_dict(lang)
    if not BURST_LIMIT.allow():
        now = time.monotonic()
        if now - BURST_LIMIT.last_warn > 30:
            BURST_LIMIT.last_warn = now
            _record("rate_limited", url=shown, source=source)
        return LinkError("rate_limited", "Слишком много ссылок подряд — MemuAI их пропускает. Подождите немного.",
                         "Too many links in a row — MemuAI skips them. Wait a little.").as_dict(lang)
    try:
        link = parse(url)
    except LinkError as exc:
        _record("rejected", url=shown, source=source, detail=exc.code)
        return exc.as_dict(lang)
    route = get_route(link.route)
    assert route is not None
    if source == "cli" and not route.cli_ok:
        _record("rejected", url=shown, route=route.name, source=source, detail="not_for_cli")
        return LinkError("blocked", "Это действие недоступно из командной строки.",
                         "This action is not available from the command line.").as_dict(lang)
    danger = route.danger
    if danger == "safe" and route.confirm_if is not None:
        try:
            if route.confirm_if(link.params):
                danger = "confirm"
        except Exception:
            danger = "confirm"
    if settings["always_ask"]:
        danger = "confirm"
    now = time.time()
    with _LOCK:
        _prune(now)
        item = Pending(id=secrets.token_urlsafe(18), link=link, route=route, source=source, danger=danger, created=now)
        _PENDING[item.id] = item
        _QUEUE_VER += 1
    _record("queued", url=shown, route=route.name, source=source, detail=danger)
    if notify and _notifier is not None:
        try:
            _notifier(item)
        except Exception:
            log.debug("deeplink notifier failed", exc_info=True)
    return {"ok": True, "id": item.id, "route": route.name, "danger": danger,
            "title": route.title[1 if lang == "en" else 0] or route.name}

def pending(lang: str = "ru", *, mark_shown: bool = False) -> List[Dict[str, Any]]:
    now = time.time()
    with _LOCK:
        _prune(now)
        items = sorted(_PENDING.values(), key=lambda p: p.created)
        if mark_shown:
            for item in items:
                if not item.shown_at:
                    item.shown_at = now
        return [p.public(lang) for p in items]

def get_pending(item_id: str) -> Optional[Pending]:
    with _LOCK:
        _prune(time.time())
        return _PENDING.get(str(item_id or ""))

def reject(item_id: str, reason: str = "cancelled") -> bool:
    with _LOCK:
        item = _PENDING.pop(str(item_id or ""), None)
    if item:
        _record("cancelled" if reason == "cancelled" else reason, url=item.link.display(300), route=item.route.name,
                source=item.source)
    return item is not None

@dataclass
class Ctx:
    """What a route handler gets besides its params."""
    lang: str = "ru"
    source: str = "os"
    inputs: Dict[str, Any] = field(default_factory=dict)
    app: Any = None
    services: Dict[str, Any] = field(default_factory=dict)
    cache: Dict[str, Any] = field(default_factory=dict)

async def _call(fn: Callable[..., Any], *args: Any) -> Any:
    result = fn(*args)
    if inspect.isawaitable(result):
        result = await result
    return result

async def preview(item_id: str, ctx: Ctx) -> Dict[str, Any]:
    item = get_pending(item_id)
    if item is None:
        return LinkError("expired", "Ссылка устарела — откройте её ещё раз.", "The link has expired — open it again.").as_dict(ctx.lang)
    if item.route.preview is None:
        return {"ok": True, "lines": []}
    ctx.source = item.source
    ctx.cache = item.cache
    try:
        result = await asyncio.wait_for(_call(item.route.preview, dict(item.link.params), ctx), timeout=25)
    except LinkError as exc:
        return exc.as_dict(ctx.lang)
    except asyncio.TimeoutError:
        return {"ok": False, "error": "timeout", "message": "Не дождались ответа." if ctx.lang != "en" else "Timed out."}
    except Exception as exc:  # noqa: BLE001
        log.warning("deeplink preview %s failed: %s", item.route.name, exc)
        return {"ok": False, "error": "preview_failed", "message": str(exc)[:300]}
    result = dict(result or {})
    result.setdefault("ok", True)
    return result

async def run(item_id: str, ctx: Ctx, *, confirmed: bool = False) -> Dict[str, Any]:
    """Execute a pending item (once).  A ``confirm`` item needs ``confirmed`` and to have been shown."""
    lang = ctx.lang
    with _LOCK:
        item = _PENDING.get(str(item_id or ""))
        if item is None or item.done or time.time() - item.created > PENDING_TTL:
            return LinkError("expired", "Ссылка устарела — откройте её ещё раз.", "The link has expired — open it again.").as_dict(lang)
        if item.danger == "confirm":
            if not confirmed:
                return LinkError("needs_confirm", "Это действие нужно подтвердить в окне MemuAI.",
                                 "This action must be confirmed in the MemuAI window.").as_dict(lang)
            if not item.shown_at or time.time() - item.shown_at < CONFIRM_MIN_SHOWN:
                return LinkError("too_fast", "Подтверждение пришло раньше, чем окно показало вопрос.",
                                 "The confirmation came before the window showed the question.").as_dict(lang)
        item.done = True
    route = item.route
    ctx.source = item.source
    ctx.cache = item.cache
    allowed_inputs = {str(x.get("id")) for x in route.inputs}
    ctx.inputs = {k: v for k, v in (ctx.inputs or {}).items() if k in allowed_inputs and isinstance(v, (str, bool, int))}
    try:
        if route.handler is not None:
            result = await asyncio.wait_for(_call(route.handler, dict(item.link.params), ctx), timeout=60)
            result = dict(result or {})
        else:
            result = {"ok": True, "ui": {"do": route.ui or route.name, **_public_params(item.link.params)}}
    except LinkError as exc:
        _record("failed", url=item.link.display(300), route=route.name, source=item.source, detail=exc.code)
        return exc.as_dict(lang)
    except Exception as exc:  # noqa: BLE001
        log.warning("deeplink %s failed: %s", route.name, exc)
        _record("failed", url=item.link.display(300), route=route.name, source=item.source, detail=type(exc).__name__)
        return {"ok": False, "error": "failed", "message": str(exc)[:300]}
    result.setdefault("ok", True)
    _record("done" if result.get("ok") else "failed", url=item.link.display(300), route=route.name,
            source=item.source, detail="confirmed" if item.danger == "confirm" else "safe")
    with _LOCK:
        _PENDING.pop(item.id, None)
    return result

def reset_for_tests() -> None:
    with _LOCK:
        _PENDING.clear()
    BURST_LIMIT.reset()
