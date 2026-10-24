"""Persistent, declarative observations owned by modules, run by the Proxy.

Source URLs and extraction rules belong to module packages. The host has no
knowledge of particular providers or leaderboards. Failed reads never advance
a baseline; a first successful read establishes it without notifications.
"""
from __future__ import annotations

import asyncio
import csv
import ipaddress
import io
import json
import re
import time
import uuid
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from . import modules_host
from .storage import load_json_with_recovery, mutate_json


def _path():
    return modules_host.default_root() / "background.json"


def _read():
    return load_json_with_recovery(str(_path()), {"jobs": {}, "events": [], "sequence": 0})


def _mutate(fn):
    def transaction(data):
        before = json.dumps(data, sort_keys=True)
        fn(data)
        return before != json.dumps(data, sort_keys=True)
    return mutate_json(str(_path()), {"jobs": {}, "events": [], "sequence": 0}, transaction, backup=False)


def require(module_id, *permissions, enabled=False):
    module_id = modules_host._safe_id(module_id)
    row = next((row for row in modules_host.list_modules(modules_host.default_root())
                if row["id"] == module_id), None)
    if row is None or (enabled and not row.get("enabled")):
        raise PermissionError("Модуль выключен или удалён / Module disabled or removed")
    if not set(permissions).issubset(row.get("permissions", [])):
        raise PermissionError("Модулю не выданы необходимые права / Module permission denied")
    return row


async def public_request(url, *, method="GET", headers=None, payload=None, limit=5_000_000, timeout=45):
    """Bounded HTTP with the same public-address checks as module web reads.

Credentials never follow a redirect to another origin and are never included
in error messages. DNS/address validation is repeated on each hop.
"""
    from .websearch import _resolve_public
    original = urlsplit(str(url))
    if headers and original.scheme != "https":
        raise ValueError("Для ключа API нужен HTTPS / API credentials require HTTPS")
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        for _ in range(6):
            await _resolve_public(url)
            async with client.stream(method, url, headers=headers or {}, json=payload) as response:
                stream = response.extensions.get("network_stream")
                peer = stream.get_extra_info("server_addr") if stream and hasattr(stream, "get_extra_info") else None
                address = peer[0] if isinstance(peer, (tuple, list)) and peer else peer
                try:
                    connected = ipaddress.ip_address(str(address).split("%", 1)[0])
                except ValueError as exc:
                    raise ValueError("Адрес сервера не подтверждён / Server address could not be verified") from exc
                if not connected.is_global:
                    raise ValueError("Локальный адрес запрещён / Private server address refused")
                if response.is_redirect:
                    target = urljoin(url, response.headers.get("location", ""))
                    if headers and urlsplit(target)[:2] != original[:2]:
                        raise ValueError("Перенаправление ключа API запрещено / Credential redirect refused")
                    url = target
                    continue
                if response.status_code >= 400:
                    error = ValueError(f"API HTTP {response.status_code}")
                    # The reason the API gave (ElevenLabs: {"detail": {"status": "bad_prompt", …}}); the message
                    # itself stays «API HTTP NNN» — callers match it. Credentials are never echoed by the APIs.
                    try:
                        error.body = (await response.aread())[:8192].decode("utf-8", "replace")
                    except Exception:
                        error.body = ""
                    raise error
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise ValueError("Ответ API слишком большой / API response too large")
                    chunks.append(chunk)
                return b"".join(chunks), response.headers.get("content-type", ""), url
    raise ValueError("Слишком много перенаправлений / Too many redirects")


def field(obj, path, default=None):
    for key in str(path or "").split(".") if path else []:
        if isinstance(obj, dict):
            obj = obj.get(key, default)
        elif isinstance(obj, list) and key.isdigit() and int(key) < len(obj):
            obj = obj[int(key)]
        else:
            return default
    return obj


def _embedded(text, spec):
    # Decode the string payloads in a React server-component stream.
    parts = []
    for match in re.finditer(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', text):
        try:
            parts.append(json.loads(match[1]))
        except ValueError:
            pass
    if parts:
        text = "".join(parts)
    marker = str(spec.get("marker") or "")
    decoder = json.JSONDecoder()
    positions = [m.end() for m in re.finditer(re.escape(marker), text)] if marker else [0]
    for position in positions[:100]:
        starts = [i for i in (text.find("[", position), text.find("{", position)) if i >= 0]
        if not starts:
            continue
        try:
            obj, _ = decoder.raw_decode(text[min(starts):])
            rows = field(obj, spec.get("items_path"))
            if isinstance(rows, list) and rows and (not spec.get("require_field")
                    or any(field(row, spec["require_field"]) is not None for row in rows)):
                return rows
        except ValueError:
            pass
    raise ValueError("Формат источника изменился / Source format changed")


def extract(text, spec):
    fmt = spec.get("format", "json")
    if fmt == "csv":
        rows = list(csv.DictReader(io.StringIO(text)))
    elif fmt == "embedded":
        rows = _embedded(text, spec)
    elif fmt == "js-records":
        # Parse a flat JavaScript data literal, never execute website code.
        rows = []
        for block in re.findall(r"\{([^{}]{1,12000})\}", text):
            values = {}
            for match in re.finditer(r'([A-Za-z_][\w]*):\s*("(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?|null|true|false)', block):
                try:
                    values[match[1]] = json.loads(match[2])
                except ValueError:
                    pass
            if values:
                rows.append(values)
    else:
        rows = field(json.loads(text), spec.get("items_path", "data"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Источник не вернул список / Source returned no records")
    if isinstance(spec.get("select"), dict):
        selection = spec["select"]
        chosen = next((row for row in rows if field(row, selection.get("key")) == selection.get("value")), None)
        rows = field(chosen, selection.get("items_path"))
        if not isinstance(rows, list) or not rows:
            raise ValueError("Категория источника недоступна / Source category unavailable")
    result = {}
    for row in rows[:10000]:
        if not isinstance(row, dict):
            continue
        identity = next((field(row, key) for key in spec.get("id_keys", ["id"])
                         if field(row, key) is not None), None)
        if identity is None or str(identity) == "":
            continue
        identity = str(identity)[:300]
        label = next((field(row, key) for key in spec.get("label_keys", ["name", "id"])
                      if field(row, key)), identity)
        keys = spec.get("value_keys", [])
        if keys == ["*"]:
            keys = [key for key in row if key not in spec.get("id_keys", ["id"])
                    and (isinstance(row[key], (int, float)) and not isinstance(row[key], bool)
                         or isinstance(row[key], str) and re.fullmatch(r"-?\d+(?:\.\d+)?", row[key].strip()))]
        values = {key: field(row, key) for key in keys}
        if values and all(value is None for value in values.values()):
            continue
        result[identity] = {"id": identity, "name": str(label)[:300], "values": values,
                            "family": _family(identity)}
    if not result:
        raise ValueError("Не удалось прочитать записи / Could not extract records")
    return result


def _family(identity):
    """Vendor prefix of a model id ("openai/gpt-5" -> "openai").

    Google lists every model as "models/<id>", so its collection prefix is not a
    family; the family filter would otherwise offer a single meaningless "models".
    """
    identity = str(identity)
    if identity.startswith("models/"):
        identity = identity[len("models/"):]
    return identity.split("/")[0] if "/" in identity else ""


# Fields that decide *which records* a source returns. Changing anything else
# (title, family filter, key) must not throw away what has already been seen,
# otherwise every Save silently re-baselines and the next new model is missed.
_DATA_IDENTITY = ("url", "format", "items_path", "id_keys", "marker", "require_field",
                  "discover", "pagination", "select", "value_keys")
_RUNTIME_KEYS = ("baseline", "seen", "last_success", "count", "families_available")


_SEEN_LIMIT = 30000


def _remember_seen(seen, records):
    """Ids already reported for a source.

    Every id the source returns now is always kept. The old code kept
    ``sorted(...)[-20000:]`` — the alphabetically *last* ids — so on a large
    source the first ids in the alphabet were dropped and came back as "new".
    Only ids that vanished from the source are trimmed when over the limit.
    """
    current = set(records)
    gone = sorted(set(seen) - current)
    room = max(0, _SEEN_LIMIT - len(current))
    return sorted(current | set(gone[-room:] if room else []))


def _headers(source):
    key = str(source.get("api_key") or "")
    headers = {"Accept": "application/json,text/plain,text/html;q=0.8"}
    if key:
        auth = source.get("auth", "bearer")
        headers[{"google": "x-goog-api-key", "anthropic": "x-api-key"}.get(auth, "Authorization")] = (
            key if auth in ("google", "anthropic") else "Bearer " + key)
    if source.get("auth") == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
    return headers


async def read_source(source):
    url = source["url"]
    candidates = []
    # Optional generic discovery steps: a page -> script -> latest CSV.
    for discovery in source.get("discover", []):
        raw, _, final_url = await public_request(url)
        text = raw.decode("utf-8", errors="replace")
        if discovery.get("kind") == "script":
            scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)', text, re.I)
            selected = [item for item in scripts if str(discovery.get("contains", "")) in item]
            if not selected:
                raise ValueError("Скрипт данных не найден / Data script not found")
            url = urljoin(final_url, selected[0])
        elif discovery.get("kind") == "date":
            # Published release lists are more specific than copyright dates or
            # other unrelated dates in a site's application bundle.
            lists = re.findall(r'\[\s*"20\d\d-\d\d-\d\d"(?:\s*,\s*"20\d\d-\d\d-\d\d")*\s*\]', text)
            dates = sorted({date for item in lists for date in json.loads(item)}, reverse=True)
            if not dates:
                dates = sorted(set(re.findall(r"20\d\d-\d\d-\d\d", text)), reverse=True)
            if not dates:
                raise ValueError("Версия данных не найдена / Data version not found")
            candidates = [urljoin(final_url, str(discovery["template"]).replace("{date}", date.replace("-", "_")))
                          for date in dates[:5]]
            url = candidates.pop(0)
        else:
            raise ValueError("Неизвестный шаг источника / Unknown source discovery step")
    result = {}
    for _ in range(30):
        while True:
            try:
                raw, _, _ = await public_request(url, headers=_headers(source) if source.get("api_key") else None)
                break
            except ValueError as exc:
                # A release can be announced before its table is published.
                # Authentication, timeouts and parser failures must still be
                # reported instead of advancing a stale baseline.
                if str(exc) not in {"API HTTP 404", "API HTTP 410"} or not candidates:
                    raise
                url = candidates.pop(0)
        text = raw.decode("utf-8", errors="replace")
        result.update(extract(text, source))
        paging = source.get("pagination")
        if not paging:
            return result
        obj = json.loads(text)
        token = field(obj, paging.get("token_path"))
        if paging.get("more_path") and not field(obj, paging["more_path"]):
            return result
        if not token:
            return result
        parsed = urlsplit(source["url"])
        query = dict(parse_qsl(parsed.query))
        query[paging.get("parameter", "pageToken")] = str(token)
        url = urlunsplit((*parsed[:3], urlencode(query), parsed.fragment))
    raise ValueError("Слишком много страниц API / API pagination limit exceeded")


def _public(job):
    copy = json.loads(json.dumps(job))
    for source in copy.get("sources", []):
        source["key_saved"] = bool(source.pop("api_key", ""))
        source.pop("lease", None)
        source.pop("seen", None)
        source.pop("baseline", None)
    return copy


def status(module_id):
    modules_host._safe_id(module_id)
    return _public(_read().get("jobs", {}).get(module_id, {"enabled": False, "sources": []}))


def configure(module_id, payload):
    require(module_id, "background", "web", "notify")
    source_specs = payload.get("sources", [])
    if not isinstance(source_specs, list) or len(source_specs) > 60:
        raise ValueError("Не больше 60 источников / Maximum 60 sources")
    clean = []
    for spec in source_specs:
        if not isinstance(spec, dict):
            raise ValueError("Некорректный источник / Invalid source")
        identity = modules_host._safe_id(spec.get("id"))
        url = str(spec.get("url", ""))[:2000]
        if not url.startswith("https://") or urlsplit(url).username or urlsplit(url).password:
            raise ValueError("Для источника нужен HTTPS / Source requires HTTPS")
        source = {key: spec[key] for key in ("format", "items_path", "id_keys", "label_keys", "value_keys",
                  "marker", "require_field", "discover", "pagination", "auth", "families", "select") if key in spec}
        source.update(id=identity, url=url, title=str(spec.get("title", identity))[:150])
        if "families" in source:
            families = source["families"] if isinstance(source["families"], list) else []
            source["families"] = sorted({str(item)[:100] for item in families if str(item).strip()})[:500]
        if "api_key" in spec:
            source["api_key"] = str(spec["api_key"])[:4000]
        if len(json.dumps(source)) > 16000 or any(item["id"] == identity for item in clean):
            raise ValueError("Некорректный источник / Invalid source")
        clean.append(source)
    def change(data):
        old = data.setdefault("jobs", {}).get(module_id, {})
        previous = {source["id"]: source for source in old.get("sources", [])}
        for source in clean:
            prior = previous.get(source["id"], {})
            if "api_key" not in source and source["url"] == prior.get("url"):
                source["api_key"] = prior.get("api_key", "")
            if prior and all(source.get(key) == prior.get(key) for key in _DATA_IDENTITY):
                source.update({key: prior[key] for key in _RUNTIME_KEYS if key in prior})
                # An unchanged source keeps its schedule too: re-saving settings
                # must not trigger (or postpone) a check on its own.
                if "last_attempt" in prior:
                    source["last_attempt"] = prior["last_attempt"]
                if "error" in prior and source.get("auth") == prior.get("auth") \
                        and ("api_key" not in spec or not spec.get("api_key")):
                    source["error"] = prior["error"]
        data["jobs"][module_id] = {"enabled": bool(payload.get("enabled", True)),
                "interval": max(300, min(86400, int(payload.get("interval", 3600)))),
                "mode": "changes" if payload.get("mode") == "changes" else "new",
                "telegram": payload.get("telegram", True) is not False,
                "revision": uuid.uuid4().hex, "sources": clean}
        return data
    _mutate(change)
    return status(module_id)


async def notify(module_id, title, text, telegram=True):
    require(module_id, "notify")
    title, text = str(title)[:150], str(text)[:10000]
    def append(data):
        # Fix the acknowledgement before the first new event of a store written by
        # an older version: older events count as shown, this one does not.
        if not isinstance(data.get("ack"), int) or isinstance(data.get("ack"), bool):
            data["ack"] = _acknowledged(data)
        data["sequence"] = int(data.get("sequence", 0)) + 1
        event = {"id": data["sequence"], "module_id": module_id, "title": title, "text": text, "at": time.time()}
        data["events"] = (data.get("events", []) + [event])[-200:]
        return data
    _mutate(append)
    try:   # Round 35: «Новые модели API» and other module notices → «Входящие» (app/inbox_hooks.py)
        from .inbox_hooks import module_event
        module_event(module_id, title, text)
    except Exception:
        pass
    from .pgdata import load_preferences
    prefs = load_preferences()
    if prefs.get("notify_windows", True):
        from .desktop_notifications import show_completion
        try:
            await asyncio.to_thread(show_completion, title, text, sound=False)
        except Exception:  # an OS toast is optional; the durable event is already stored
            pass
    if telegram:
        from .telegram_bot import send_notification
        # Loudness follows the user's Telegram notification mode (silent/sound).
        await send_notification(title, text, kind="module")


async def scan(module_id, *, force=False):
    row = require(module_id, "background", "web", "notify", enabled=True)
    job = _read().get("jobs", {}).get(module_id, {})
    if not job.get("enabled") and not force:
        return status(module_id)
    revision = job.get("revision")
    for original in job.get("sources", []):
        source_id, lease = original["id"], uuid.uuid4().hex
        claimed = []
        def claim(data):
            current = data.get("jobs", {}).get(module_id, {})
            if current.get("revision") != revision:
                return data
            for source in current.get("sources", []):
                if source["id"] != source_id or source.get("lease", {}).get("until", 0) > time.time():
                    continue
                if not force and time.time() - source.get("last_attempt", 0) < current["interval"]:
                    continue
                source["lease"] = {"id": lease, "until": time.time() + 180}
                source["last_attempt"] = time.time()
                claimed.append(dict(source))
            return data
        _mutate(claim)
        if not claimed:
            continue
        source, messages = claimed[0], []
        try:
            # Keep the complete paginated/discovery read below its cross-process
            # lease, so a second scan cannot steal an unfinished result.
            records = await asyncio.wait_for(read_source(source), timeout=150)
            require(module_id, "background", "web", "notify", enabled=True)
            families = set(source.get("families", []))
            visible = {key: value for key, value in records.items() if not families or value["family"] in families}
            baseline, seen = source.get("baseline"), set(source.get("seen", []))
            if baseline is not None:
                for key, value in visible.items():
                    if key not in seen:
                        messages.append("+ " + value["name"] + " · " + key)
                    elif job.get("mode") == "changes" and key in baseline and baseline[key] != value["values"]:
                        before = baseline[key]
                        details = ", ".join(f"{field_name}: {before.get(field_name)} → {number}"
                                            for field_name, number in value["values"].items() if before.get(field_name) != number)
                        messages.append(value["name"] + ": " + details)
                if job.get("mode") == "changes":
                    # A family filter change is not a removal: only ids gone from the
                    # source itself are reported.
                    messages.extend("− " + key for key in baseline if key not in records)
            updates = {"baseline": {key: value["values"] for key, value in visible.items()},
                       "seen": _remember_seen(seen, records), "count": len(visible),
                       "families_available": sorted({value["family"] for value in records.values() if value["family"]}),
                       "last_success": time.time(), "error": ""}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Exception URLs can include credentials; only emit bounded safe errors.
            message = str(exc) if isinstance(exc, (ValueError, PermissionError)) else type(exc).__name__
            if source.get("api_key"):
                message = message.replace(source["api_key"], "[redacted]")
            updates = {"error": message[:350]}
            messages = []
        committed = []
        def commit(data):
            current = data.get("jobs", {}).get(module_id, {})
            if current.get("revision") != revision or (not current.get("enabled") and not force):
                return data
            for item in current.get("sources", []):
                if item["id"] == source_id and item.get("lease", {}).get("id") == lease:
                    item.update(updates)
                    item.pop("lease", None)
                    committed.append(True)
            return data
        _mutate(commit)
        if committed and messages:
            title = row.get("title", module_id) + " · " + source["title"]
            await notify(module_id, title, "\n".join(messages[:50]) + (f"\n+{len(messages)-50}" if len(messages) > 50 else ""), job.get("telegram", True))
    return status(module_id)


def _acknowledged(data):
    """Sequence number up to which MemuAI has already shown events to the user.

    Round 21: this used to live only in the page's localStorage, but the desktop
    window runs WebView2 in pywebview's default private mode, so localStorage is
    wiped on every launch: the cursor fell back to 0 and the last stored events
    were shown again as "new" after each start. The acknowledgement now lives in
    background.json next to the events (one store for Proxy and desktop). A store
    written by an older version has no ``ack`` yet: everything already stored
    counts as shown, so the upgrade itself does not replay old notifications.
    """
    sequence = int(data.get("sequence", 0) or 0)
    ack = data.get("ack")
    if not isinstance(ack, int) or isinstance(ack, bool) or ack < 0:
        return sequence
    return min(ack, sequence)


def _seen_cursors(data):
    raw = data.get("seen_events")
    return {str(key): int(value) for key, value in raw.items()
            if isinstance(value, int) and not isinstance(value, bool)} if isinstance(raw, dict) else {}


def events(after=0, module_id=None):
    data = _read()
    rows = [event for event in data.get("events", []) if event["id"] > after
            and (module_id is None or event.get("module_id") == module_id)]
    result = {"cursor": data.get("sequence", 0), "events": rows, "acknowledged": _acknowledged(data)}
    if module_id is not None:
        result["seen"] = _seen_cursors(data).get(module_id, 0)
    return result


def pending_events():
    """Events MemuAI has not shown yet (id > acknowledged); persists the migration."""
    def migrate(data):
        if not isinstance(data.get("ack"), int) or isinstance(data.get("ack"), bool):
            data["ack"] = _acknowledged(data)
        return data
    _mutate(migrate)
    data = _read()
    ack = _acknowledged(data)
    return {"cursor": data.get("sequence", 0), "acknowledged": ack,
            "events": [event for event in data.get("events", []) if event["id"] > ack]}


def acknowledge(cursor):
    """Remember that events up to ``cursor`` were shown; never moves backwards."""
    cursor = max(0, int(cursor))
    def change(data):
        sequence = int(data.get("sequence", 0) or 0)
        data["ack"] = max(_acknowledged(data), min(cursor, sequence))
        return data
    _mutate(change)
    return {"acknowledged": _acknowledged(_read())}


def mark_seen(module_id, cursor):
    """The module's own "seen" mark (its unread badge); also acknowledges toasts."""
    modules_host._safe_id(module_id)
    cursor = max(0, int(cursor))
    def change(data):
        sequence = int(data.get("sequence", 0) or 0)
        value = min(cursor, sequence)
        seen = _seen_cursors(data)
        seen[module_id] = max(seen.get(module_id, 0), value)
        data["seen_events"] = seen
        data["ack"] = max(_acknowledged(data), value)
        return data
    _mutate(change)
    data = _read()
    return {"seen": _seen_cursors(data).get(module_id, 0), "acknowledged": _acknowledged(data)}


async def background_loop():
    from .safe_mode import session_active
    while True:
        # Round 29: module jobs pause while MemuAI runs in safe mode (app/safe_mode.py).
        for module_id in ([] if session_active() else list(_read().get("jobs", {}))):
            try:
                await scan(module_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Disabled/deleted modules and revoked permissions are quiet.
                pass
        await asyncio.sleep(30)
