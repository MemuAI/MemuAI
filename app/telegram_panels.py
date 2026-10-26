"""Panels ↔ Telegram (Round 24).

* **Subscriptions.** Any "slow" panel (weather, news/RSS, countdown, To-Do,
  currency/crypto/stock, sports, calendar, GitHub, web/custom AI panels) can be
  subscribed to: updates arrive in the paired Telegram chat. Stored per profile
  in ``telegram_panels.json`` next to ``panels.json`` (never inside the panel
  record, so the panels schema/validators stay untouched).
  Modes: ``changes`` — only significant changes (per kind, see
  ``significant``), ``every`` — any visible change, ``digest`` — one daily
  message at ``at`` (HH:MM) with all digest panels.
* **Anti-spam.** Deduplication by signature, a minimum gap per panel
  (``min_gap_minutes``; a change inside the gap is *coalesced*: the newest
  state is sent when the gap ends), a global cap per hour, and quiet hours of
  the bot (``telegram_quiet_hours``): delivered silently, or postponed when
  the subscription says ``quiet: "defer"``.
* **One renderer.** ``resolve_blocks()`` substitutes the panel data into its
  spec blocks (the same ``spec``/``data`` the window renders); Telegram HTML
  (``render_html``) and the Mini App cards (``card``) are built from that, no
  per-kind duplicate of the panel logic.
* **Where it runs.** ``check_all()`` is called from ``panels.tick()`` in the
  MemuAI window process (it is cheap: no network, only file reads) and sends
  via the Bot API directly. The bot (Proxy process) uses ``show_list``/
  ``handle_callback`` for ``/panels``; a refresh asked from Telegram sets
  ``next_refresh = now`` so the window's scheduler picks it up within ~20 s.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    from fastapi import Request
except Exception:  # pragma: no cover — the bot itself does not need FastAPI
    Request = Any  # type: ignore[misc,assignment]

log = logging.getLogger("memuai.telegram")

FILE_NAME = "telegram_panels.json"
NOTIFY_KINDS = ("weather", "news", "rss", "countdown", "todo", "currency", "crypto", "stock", "sports",
                "calendar", "github", "web", "custom", "notes")
MODES = ("changes", "every", "digest")
QUIET = ("silent", "defer")
DEFAULT_SETTINGS = {"min_gap_minutes": 30, "max_per_hour": 6, "digest_at": "09:00"}

THRESHOLDS = {"currency": 0.01, "crypto": 0.05, "stock": 0.03}
COUNTDOWN_MILESTONES = (30, 14, 7, 3, 1, 0)
CHECK_EVERY = 60.0
_PID = re.compile(r"^p_[a-z0-9]{6,24}$")
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_BIND = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z0-9_]+)*)\s*\}\}")
_FULL_BIND = re.compile(r"^\s*\{\{\s*([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z0-9_]+)*)\s*\}\}\s*$")
_state: Dict[str, Any] = {"last_check": 0.0, "sender": None}

def _path():
    from .pgdata import MEMU_DIR, _profile_data_name
    try:
        name = _profile_data_name(FILE_NAME)
    except Exception:
        name = FILE_NAME
    return MEMU_DIR / name

def _hhmm(value: Any) -> str:
    """"9:00" → "09:00" (digest times are compared as strings)."""
    match = _HHMM.match(str(value or "").strip())
    return f"{int(match.group(1)):02d}:{match.group(2)}" if match else ""

def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default

def _clip_html(lines: List[str], limit: int) -> str:
    """Join balanced HTML lines without cutting a tag/entity in half."""
    out: List[str] = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > limit:
            if not out:
                out.append(html.escape(html.unescape(re.sub(r"<[^>]*>", "", line)), quote=False)[: max(0, limit - 1)] + "…")
            else:
                out.append("…")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out).strip()

def _clean_sub(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    mode = raw.get("mode") if raw.get("mode") in MODES else "changes"
    return {"on": bool(raw.get("on")), "mode": mode,
            "at": _hhmm(raw.get("at")),
            "quiet": raw.get("quiet") if raw.get("quiet") in QUIET else "silent"}

def _normalize(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    settings = dict(DEFAULT_SETTINGS)
    given = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
    try:
        settings["min_gap_minutes"] = max(0, min(24 * 60, int(given.get("min_gap_minutes", settings["min_gap_minutes"]))))
        settings["max_per_hour"] = max(1, min(60, int(given.get("max_per_hour", settings["max_per_hour"]))))
    except (TypeError, ValueError):
        pass
    if _hhmm(given.get("digest_at")):
        settings["digest_at"] = _hhmm(given.get("digest_at"))
    subs = {pid: _clean_sub(sub) for pid, sub in (raw.get("subs") or {}).items()
            if isinstance(pid, str) and _PID.match(pid)} if isinstance(raw.get("subs"), dict) else {}
    sent = {pid: row for pid, row in (raw.get("sent") or {}).items()
            if isinstance(pid, str) and _PID.match(pid) and isinstance(row, dict)} \
        if isinstance(raw.get("sent"), dict) else {}
    log_ = [float(x) for x in (raw.get("log") or []) if isinstance(x, (int, float))][-100:] \
        if isinstance(raw.get("log"), list) else []
    digest = raw.get("digest") if isinstance(raw.get("digest"), dict) else {}
    return {"version": 1, "settings": settings, "subs": subs, "sent": sent, "log": log_,
            "digest": {"day": str(digest.get("day") or "")}}

def load() -> Dict[str, Any]:
    from .storage import load_json_with_recovery
    return _normalize(load_json_with_recovery(_path(), {}))

def mutate(mutator) -> Tuple[Dict[str, Any], Any]:
    from .storage import mutate_json
    box: Dict[str, Any] = {}

    def update(raw: Any):
        doc = _normalize(raw)
        box["result"] = mutator(doc)
        return True, doc

    doc = mutate_json(_path(), {}, update, backup=False)
    return (doc if isinstance(doc, dict) else load()), box.get("result")

def eligible(panel: Dict[str, Any]) -> bool:
    return str(panel.get("kind") or "") in NOTIFY_KINDS

def subscribe(pid: str, *, on: Optional[bool] = None, mode: Optional[str] = None,
              at: Optional[str] = None, quiet: Optional[str] = None) -> Dict[str, Any]:
    """Change a subscription; returns the clean subscription. Unknown panel → ValueError."""
    if not _PID.match(str(pid or "")):
        raise ValueError("bad panel id")
    from . import panels
    doc = panels.load()
    panel = next((p for p in panels.active(doc) if p["id"] == pid), None)
    if panel is None:
        raise ValueError("panel not found")
    if not eligible(panel):
        raise ValueError("this panel kind has no Telegram updates")

    def apply(data: Dict[str, Any]) -> Dict[str, Any]:
        sub = dict(data["subs"].get(pid) or _clean_sub({}))
        was_on = sub.get("on")
        if on is not None:
            sub["on"] = bool(on)
        if mode is not None:
            sub["mode"] = mode
        if at is not None:
            sub["at"] = at
        if quiet is not None:
            sub["quiet"] = quiet
        sub = _clean_sub(sub)
        data["subs"][pid] = sub
        if sub["on"] and not was_on:

            data["sent"][pid] = _baseline(panel)
        return sub

    _doc, sub = mutate(apply)
    return sub

def subscription(pid: str, doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    doc = doc or load()
    return dict(doc["subs"].get(pid) or _clean_sub({}))

def forget_missing(active_ids: List[str]) -> None:
    keep = set(active_ids)

    def apply(data):
        for key in ("subs", "sent"):
            for pid in list(data[key]):
                if pid not in keep:
                    data[key].pop(pid, None)

    mutate(apply)

def _lookup(data: Dict[str, Any], key: str, path: str) -> Any:
    value: Any = data.get(key)
    for part in [p for p in path.split(".") if p]:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value

def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "✓" if value else "✗"
    if isinstance(value, float):
        text = f"{value:,.2f}".replace(",", " ")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, (list, dict)):
        return ""
    return str(value)

def _resolve(value: Any, data: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        full = _FULL_BIND.match(value)
        if full:
            return _lookup(data, full.group(1), full.group(2) or "")
        return _BIND.sub(lambda m: _fmt(_lookup(data, m.group(1), m.group(2) or "")), value)
    if isinstance(value, list):
        return [_resolve(v, data) for v in value]
    if isinstance(value, dict):
        return {k: _resolve(v, data) for k, v in value.items()}
    return value

def resolve_blocks(panel: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The panel's spec blocks with its data substituted (``row`` flattened)."""
    data = panel.get("data") if isinstance(panel.get("data"), dict) else {}
    out: List[Dict[str, Any]] = []

    def walk(blocks: Any) -> None:
        for block in blocks if isinstance(blocks, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "row":
                walk(block.get("blocks"))
                continue
            out.append(_resolve(block, data))

    walk(((panel.get("spec") or {}).get("blocks")))
    return out

def _todo_lists(panel: Dict[str, Any]) -> List[Dict[str, Any]]:
    todo = panel.get("todo")
    if isinstance(todo, dict):
        return [row for row in todo.get("lists") or [] if isinstance(row, dict)]
    try:
        from . import panels_local
        local = panels_local.load()
    except Exception:
        return []
    wanted = list((panel.get("options") or {}).get("lists") or [])
    by_id = {row.get("id"): row for row in local.get("lists") or [] if isinstance(row, dict)}
    rows = [by_id[lid] for lid in wanted if lid in by_id]
    return rows or list(by_id.values())[:1]

def _open_tasks(panel: Dict[str, Any]) -> List[Dict[str, Any]]:
    tasks = []
    for lst in _todo_lists(panel):
        for item in lst.get("items") or []:
            if isinstance(item, dict) and not item.get("done") and item.get("title"):
                tasks.append(item)
    return tasks

def _today() -> str:
    return datetime.now().date().isoformat()

def _due_tasks(panel: Dict[str, Any]) -> List[Dict[str, Any]]:
    today = _today()
    return [t for t in _open_tasks(panel) if str(t.get("due") or "")[:10] and str(t.get("due"))[:10] <= today]

def _items(value: Any) -> List[Dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []

def _title_and_error(panel: Dict[str, Any], lang: str = "") -> Tuple[str, str]:
    """Round 26: the built-in «Погода» panel and its «Укажите город…» wall follow the panels' language (the PC
    window) — in an English bot / Mini App they stayed Russian. ``lang`` is the bot's language."""
    from . import panels
    title = str(panel.get("title") or "")
    wall = panel.get("wall") or {}
    error = str(wall.get("message") or "") if wall else ""
    if lang in ("ru", "en") and panel.get("kind") == "weather":
        if not panel.get("title_custom") and title in ("Погода", "Weather"):
            title = "Weather" if lang == "en" else "Погода"
        if wall.get("status") == "needs_location:weather":
            error = panels._location_wall({"lang": lang})["message"]
    if error and lang in ("ru", "en") and bool(re.search("[А-Яа-яЁё]", error)) != (lang == "ru"):

        kind = str(wall.get("status") or "").split(":")[0]
        ru = lang == "ru"
        error = {"needs_connector": "Нужно подключение к сервису — откройте настройки панели в MemuAI." if ru
                 else "A service connection is needed — open the panel settings in MemuAI.",
                 "needs_login": "Нужен вход на сайт — откройте панель в MemuAI." if ru
                 else "A site sign-in is needed — open the panel in MemuAI.",
                 "paywall": "Страница доступна только по подписке." if ru else "The page is for subscribers only.",
                 "source_gone": "Источник больше не отвечает или не нашёл данные." if ru
                 else "The source is gone or has no such data."}.get(
            kind, "Не удалось обновить — попробую позже." if ru else "Could not refresh — will retry later.")
    return title, error

def _in_language(panel: Dict[str, Any], lang: str, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Round 27: a built-in panel's spec holds the labels of the window's panels language («Макс / мин»,
    «По часам», «Ощущается как…»): an English bot showed them in Russian. The spec is rebuilt in the bot's
    ``lang`` (the data stay as they are). AI panels keep the spec their model wrote."""
    source = panel.get("source") if isinstance(panel.get("source"), dict) else {}
    if lang not in ("ru", "en") or not panel.get("kind") or source.get("type") in (None, "", "ai"):
        return panel
    from . import panels
    try:
        settings = settings if settings is not None else panels.load()["settings"]
        if panels._lang(settings) == lang:
            return panel
        _title, spec = panels.typed_spec(panel["kind"], source.get("params") or {}, {**settings, "lang": lang},
                                         panel.get("options"))
    except Exception as exc:
        log.debug("panel spec in %s failed: %s", lang, exc)
        return panel
    return {**panel, "spec": spec}

def card(panel: Dict[str, Any], lang: str = "", settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A compact, renderer-neutral view of a panel (Mini App cards, Telegram) in the bot's ``lang``."""
    panel = _in_language(panel, lang, settings)
    blocks = resolve_blocks(panel)
    title, error = _title_and_error(panel, lang)
    view: Dict[str, Any] = {"id": panel.get("id"), "kind": panel.get("kind"),
                            "title": title, "subtitle": "",
                            "status": panel.get("status"), "updated": panel.get("updated") or panel.get("last_refresh"),
                            "error": error,
                            "metric": None, "stats": [], "items": [], "text": [], "links": [],
                            "countdown": None, "tasks": [], "sources": [], "image": None}
    for block in blocks:
        kind = block.get("type")
        if kind == "title":
            if not view["subtitle"]:
                title = _fmt(block.get("text"))
                parts = [title] if title and title != view["title"] else []
                if _fmt(block.get("subtitle")):
                    parts.append(_fmt(block.get("subtitle")))
                view["subtitle"] = " · ".join(parts)
        elif kind in ("metric", "gauge") and view["metric"] is None:
            view["metric"] = {"value": _fmt(block.get("value")), "unit": _fmt(block.get("unit")),
                              "caption": _fmt(block.get("caption") or block.get("label")),
                              "delta": _fmt(block.get("delta")), "trend": _fmt(block.get("trend"))}
        elif kind in ("stats", "strip", "bars", "meters"):
            for row in _items(block.get("items"))[: max(0, _int(block.get("max"), 8) or 8)]:
                label = _fmt(row.get("label"))
                value = _fmt(row.get("value"))
                if re.search(r"\w", value):
                    view["stats"].append({"label": label, "value": (value + (" " + _fmt(row.get("unit")) if row.get("unit") else "")).strip(),
                                          "sub": _fmt(row.get("sub"))})
        elif kind == "list":
            for row in _items(block.get("items"))[: max(0, _int(block.get("max"), 10) or 10)]:
                if _fmt(row.get("title")):
                    view["items"].append({"title": _fmt(row.get("title")), "subtitle": _fmt(row.get("subtitle")),
                                          "value": _fmt(row.get("value")), "url": _fmt(row.get("url"))})
        elif kind in ("text", "note") and _fmt(block.get("text")).strip():
            view["text"].append(_fmt(block.get("text")))
        elif kind == "badge" and _fmt(block.get("text")):
            view["text"].append(_fmt(block.get("text")))
        elif kind == "link" and _fmt(block.get("url")):
            view["links"].append({"text": _fmt(block.get("text")) or _fmt(block.get("url")), "url": _fmt(block.get("url"))})
        elif kind == "countdown":
            data = panel.get("data") or {}
            view["countdown"] = {"target": _fmt(block.get("target")), "days_left": data.get("days_left"),
                                 "label": _fmt(data.get("target_label"))}
            if view["countdown"]["days_left"] is None and view["countdown"]["target"]:

                from . import panels as _panels
                iso = _panels._lenient_target(view["countdown"]["target"])
                if iso:
                    try:
                        moment = datetime.fromisoformat(iso)
                        left = moment.timestamp() - datetime.now().timestamp()
                        view["countdown"]["days_left"] = max(0, int(left // 86400))
                        view["countdown"]["target"] = iso
                        view["countdown"]["label"] = view["countdown"]["label"] or _fmt(block.get("label")) or (
                            moment.strftime("%d.%m.%Y" if lang != "en" else "%b %d, %Y"))
                    except (TypeError, ValueError, OverflowError):
                        pass
        elif kind == "image" and view["image"] is None and re.match(r"^https?://", _fmt(block.get("src"))):

            view["image"] = {"src": _fmt(block.get("src")), "caption": _fmt(block.get("caption") or block.get("alt")),
                             "url": _fmt(block.get("url"))}
            if view["image"]["caption"]:
                view["text"].append(view["image"]["caption"])
        elif kind == "progress" and block.get("value") not in (None, ""):
            view["stats"].append({"label": _fmt(block.get("label")), "value": _fmt(block.get("value")) + " %", "sub": ""})
    if panel.get("kind") == "todo":
        view["tasks"] = [{"title": str(t.get("title"))[:200], "due": str(t.get("due") or ""),
                          "priority": _int(t.get("priority"))} for t in _open_tasks(panel)[:30]]
    if panel.get("kind") == "notes" and not view["text"]:
        note = panel.get("note") if isinstance(panel.get("note"), dict) else {}
        if note.get("text"):
            view["text"].append(str(note["text"])[:2000])
    view["sources"] = [{"title": str(s.get("title") or s.get("url") or "")[:80], "url": str(s.get("url") or "")}
                       for s in (panel.get("sources") or []) if isinstance(s, dict) and s.get("url")][:4]
    return view

def _e(text: Any) -> str:
    return html.escape(str(text or ""), quote=False)

def render_html(panel: Dict[str, Any], lang: str = "ru", *, limit_items: int = 8,
                settings: Optional[Dict[str, Any]] = None) -> str:
    """Telegram HTML for one panel (≤ ~3500 chars). ``settings`` — the panels settings, when already loaded."""
    view = card(panel, lang, settings)
    ru = lang != "en"
    lines = [f"<b>{_e(view['title'] or ('Панель' if ru else 'Panel'))}</b>"]
    if view["subtitle"]:
        lines.append(f"<i>{_e(view['subtitle'])}</i>")
    metric = view["metric"]
    if metric and metric["value"]:
        unit = metric["unit"] or ""

        gap = "" if not unit or unit[:1] in "°′″" else " "
        line = f"<b>{_e(metric['value'])}</b>{gap}{_e(unit)}"
        if metric["delta"]:
            arrow = {"up": "▲", "down": "▼"}.get(metric["trend"], "")
            line += f"  {arrow}{_e(metric['delta'])}"
        lines.append(line)
        if metric["caption"]:
            lines.append(_e(metric["caption"]))
    countdown = view["countdown"] or {}
    if countdown and countdown.get("days_left") is None and countdown.get("target"):

        try:
            moment = datetime.fromisoformat(str(countdown["target"]).replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.astimezone()
            left = moment.timestamp() - datetime.now().timestamp()
            countdown["days_left"] = max(0, int(left // 86400)) if left > 0 else 0
            countdown["label"] = countdown.get("label") or moment.strftime("%d.%m.%Y" if ru else "%b %d, %Y")
        except (TypeError, ValueError, OverflowError):
            pass
    if countdown and countdown.get("days_left") is not None:
        days = _int(countdown["days_left"])
        label = countdown.get("label") or ""
        if label and label in (view["subtitle"] or ""):
            label = ""
        lines.append(("⏳ Осталось дней: " if ru else "⏳ Days left: ") + f"<b>{days}</b>" + (f" · {_e(label)}" if label else ""))
    if view["stats"]:
        lines.append("")
        for row in view["stats"][:10]:
            text = f"{_e(row['label'])}: <b>{_e(row['value'])}</b>" if row["label"] else f"<b>{_e(row['value'])}</b>"
            if row.get("sub"):
                text += f" <i>{_e(row['sub'])}</i>"
            lines.append("• " + text)
    if view["items"]:
        lines.append("")
        for row in view["items"][:limit_items]:
            title = _e(row["title"][:200])
            if row["url"].startswith(("https://", "http://")):
                title = f'<a href="{html.escape(row["url"], quote=True)}">{title}</a>'
            extra = " — " + _e(row["value"]) if row["value"] else ""
            lines.append("• " + title + extra)
    if view["tasks"]:
        lines.append("")
        today = _today()
        for task in view["tasks"][:15]:
            due = task["due"][:10]
            mark = "🔴" if due and due < today else ("🟡" if due == today else "▫️")
            lines.append(f"{mark} {_e(task['title'])}" + (f" <i>({_e(task['due'].replace('T', ' '))})</i>" if due else ""))
    elif panel.get("kind") == "todo":
        lines.append("✅ " + ("Открытых задач нет" if ru else "No open tasks"))
    for text in view["text"][:3]:
        lines.append("")
        lines.append(_e(text[:1200]))
    if view["error"]:
        lines.append("")
        lines.append("⚠️ " + _e(str(view["error"])[:300]))
    if view["updated"]:
        try:
            stamp = datetime.fromtimestamp(float(view["updated"])).strftime("%d.%m %H:%M")
            lines.append("")
            lines.append(("🕑 Обновлено " if ru else "🕑 Updated ") + stamp)
        except (TypeError, ValueError, OSError):
            pass
    return _clip_html(lines, 3800)

def _number(text: Any) -> Optional[float]:
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    match = re.search(r"-?\d[\d\s\u00a0]*(?:[.,]\d+)?", str(text or ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except ValueError:
        return None

def _digest(value: Any) -> str:
    return hashlib.sha1(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]

def _visible_signature(panel: Dict[str, Any]) -> str:
    view = card(panel)
    view = {k: v for k, v in view.items() if k not in ("updated", "status", "sources")}
    return _digest(view)

def _baseline(panel: Dict[str, Any]) -> Dict[str, Any]:
    """What a panel looks like "now", for comparisons later."""
    view = card(panel)
    base: Dict[str, Any] = {"sig": _visible_signature(panel), "at": 0.0}
    kind = panel.get("kind")
    if view["metric"]:
        base["num"] = _number(view["metric"]["value"])
    if kind == "weather":
        base["cond"] = view["subtitle"]
    if kind in ("news", "rss"):
        base["items"] = [_digest(row.get("url") or row.get("title")) for row in view["items"]][:40]
    if kind == "countdown":
        base["days"] = (panel.get("data") or {}).get("days_left")
    if kind == "todo":
        base["due"] = sorted(_digest(t.get("id") or t.get("title")) for t in _due_tasks(panel))
    return base

def significant(panel: Dict[str, Any], before: Dict[str, Any], mode: str = "changes") -> Tuple[bool, str, Dict[str, Any]]:
    """(notify?, headline, new baseline). ``before`` is the last delivered baseline."""
    now = _baseline(panel)
    if not before or not before.get("sig"):
        return False, "", now
    if now["sig"] == before.get("sig"):
        return False, "", now
    if mode == "every":
        return True, "", now
    kind = panel.get("kind")
    if kind == "weather":
        old, new = before.get("num"), now.get("num")
        if now.get("cond") and now.get("cond") != before.get("cond"):
            return True, "cond", now
        if old is not None and new is not None and abs(new - old) >= 4:
            return True, "temp", now
        return False, "", {**now, "sig": before["sig"], "num": old, "cond": before.get("cond")}
    if kind in THRESHOLDS:
        old, new = before.get("num"), now.get("num")
        if old and new is not None and abs(new - old) / abs(old) >= THRESHOLDS[kind]:
            return True, "move", now

        return False, "", {**now, "sig": before["sig"], "num": old}
    if kind in ("news", "rss"):
        fresh = [h for h in now.get("items") or [] if h not in set(before.get("items") or [])]
        if fresh:
            return True, f"new:{len(fresh)}", {**now, "fresh": fresh}
        return False, "", now
    if kind == "countdown":
        days, prev = now.get("days"), before.get("days")
        if days is not None and days != prev and days in COUNTDOWN_MILESTONES:
            return True, f"days:{days}", now
        return False, "", now
    if kind == "todo":
        added = [h for h in now.get("due") or [] if h not in set(before.get("due") or [])]
        if added:
            return True, f"due:{len(added)}", now
        return False, "", now
    return True, "", now

def _quiet(prefs: Dict[str, Any], now: Optional[float] = None) -> bool:
    try:
        from .telegram_bot import _in_quiet_hours
        return bool(_in_quiet_hours(prefs, None if now is None else datetime.fromtimestamp(now)))
    except Exception:
        return False

def _headline(panel: Dict[str, Any], reason: str, lang: str) -> str:
    ru = lang != "en"
    if reason == "cond":
        return "🌦 Погода изменилась" if ru else "🌦 The weather changed"
    if reason == "temp":
        return "🌡 Заметно изменилась температура" if ru else "🌡 The temperature changed noticeably"
    if reason == "move":
        return "📈 Заметное движение курса" if ru else "📈 A notable move"
    if reason.startswith("new:"):
        n = reason.split(":", 1)[1]
        return (f"📰 Новое: {n}" if ru else f"📰 New: {n}")
    if reason.startswith("days:"):
        n = int(reason.split(":", 1)[1])
        if n == 0:
            return "🎉 Событие сегодня!" if ru else "🎉 It's today!"
        return (f"⏳ Осталось дней: {n}" if ru else f"⏳ {n} days left")
    if reason.startswith("due:"):
        return "✅ Задачи на сегодня" if ru else "✅ Tasks due today"
    return "🔔 Панель обновилась" if ru else "🔔 Panel updated"

def _tab_url(url: str) -> str:
    """Mini App address that opens the «Панели» tab. A query parameter, not a
    hash: Telegram puts the launch data (tgWebAppData) into the hash."""
    base = str(url or "").split("#", 1)[0]
    return base + ("&" if "?" in base else "?") + "tab=panels"

def keyboard(pid: str, lang: str, *, subscribed: bool = True, webapp_url: Optional[str] = None) -> Dict[str, Any]:
    ru = lang != "en"
    rows = [[{"text": "🔄 " + ("Обновить" if ru else "Refresh"), "callback_data": f"pnlr:{pid}"},
             {"text": ("🔕 Не присылать" if ru else "🔕 Mute") if subscribed else ("🔔 Присылать" if ru else "🔔 Notify"),
              "callback_data": f"pnlsub:{pid}"}],
            [{"text": "📊 " + ("Все панели" if ru else "All panels"), "callback_data": "panels"}]]
    if webapp_url:
        rows[1].append({"text": "📱 Mini App", "web_app": {"url": _tab_url(webapp_url)}})
    return {"inline_keyboard": rows}

async def _default_sender(prefs: Dict[str, Any], text: str, markup: Optional[Dict[str, Any]], silent: bool) -> bool:
    import httpx
    from .telegram_api import BotApi
    token = str(prefs.get("telegram_bot_token") or "").strip()
    chat_id = str(prefs.get("telegram_chat_id") or "").strip()
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                               "link_preview_options": {"is_disabled": True}}
    if markup:
        payload["reply_markup"] = markup
    if silent:
        payload["disable_notification"] = True
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10)) as client:
        await BotApi(client, token).call("sendMessage", essential=True, **payload)
    return True

def set_sender(sender) -> None:
    """Tests replace the delivery function: ``sender(prefs, html, markup, silent)``."""
    _state["sender"] = sender

def _can_send(prefs: Dict[str, Any]) -> bool:
    return bool(prefs.get("telegram_enabled") and str(prefs.get("telegram_bot_token") or "").strip()
                and str(prefs.get("telegram_chat_id") or "").strip()
                and prefs.get("telegram_notify_panels", True))

def _webapp_url() -> Optional[str]:
    try:
        from .tg_webapp import webapp_url
        return webapp_url()
    except Exception:
        return None

async def check_all(*, now: Optional[float] = None, force: bool = False,
                    prefs: Optional[Dict[str, Any]] = None) -> int:
    """Compare subscribed panels with what was last delivered; send what is due.

    Returns the number of messages sent. Safe to call often (throttled to
    ``CHECK_EVERY`` unless ``force``)."""
    now = time.time() if now is None else now
    if not force and now - _state["last_check"] < CHECK_EVERY:
        return 0
    _state["last_check"] = now
    if prefs is None:
        from .pgdata import load_preferences
        prefs = await asyncio.to_thread(load_preferences)
    if not _can_send(prefs):
        return 0
    doc = await asyncio.to_thread(load)
    if not any(sub.get("on") for sub in doc["subs"].values()):
        return 0
    from . import panels
    pdoc = await asyncio.to_thread(panels.load)
    by_id = {p["id"]: p for p in panels.active(pdoc)}
    lang = _lang_of(prefs)
    quiet = _quiet(prefs, now)
    settings = doc["settings"]
    gap = float(settings["min_gap_minutes"]) * 60
    recent = [t for t in doc["log"] if now - t < 3600]
    budget = max(0, int(settings["max_per_hour"]) - len(recent))
    outgoing: List[Tuple[str, str, Dict[str, Any], bool]] = []
    updates: Dict[str, Dict[str, Any]] = {}
    webapp = _webapp_url()
    digest_due = digest_wait = False
    digest_rows: List[str] = []
    today = datetime.fromtimestamp(now).date().isoformat()
    clock = datetime.fromtimestamp(now).strftime("%H:%M")
    for pid, sub in doc["subs"].items():
        panel = by_id.get(pid)
        if not sub.get("on") or panel is None or not eligible(panel):
            continue
        if panel.get("kind") in ("todo", "notes"):
            panel = panels.public_panel(panel)
        if sub["mode"] == "digest":
            at = _hhmm(sub.get("at")) or _hhmm(settings["digest_at"]) or "09:00"
            if doc["digest"].get("day") != today and clock >= at:
                if quiet and sub.get("quiet") == "defer":
                    digest_wait = True
                    continue
                digest_due = True
                digest_rows.append(render_html(panel, lang, limit_items=5, settings=pdoc["settings"]))
                updates[pid] = _baseline(panel)
            continue
        before = doc["sent"].get(pid) or {}
        notify, reason, after = significant(panel, before, sub["mode"])
        if not notify:
            if after.get("sig") != before.get("sig") or not before:
                updates[pid] = {**after, "at": before.get("at", 0.0)}
            continue
        if now - float(before.get("at") or 0) < gap:
            continue
        if quiet and sub.get("quiet") == "defer":
            continue
        if budget <= 0:
            continue
        budget -= 1
        text = _headline(panel, reason, lang) + "\n\n" + render_html(panel, lang, settings=pdoc["settings"])
        outgoing.append((pid, text, keyboard(pid, lang, webapp_url=webapp), quiet))
        updates[pid] = {**after, "at": now}
    if digest_wait:
        digest_due = False
    if digest_due and digest_rows and budget > 0:
        title = "🗞 <b>" + ("Сводка панелей" if lang == "ru" else "Panels digest") + "</b>"
        body = _clip_html([title, ""] + "\n\n— — —\n\n".join(digest_rows).split("\n"), 4000)
        outgoing.append(("", body, {"inline_keyboard": [[{"text": "📊 " + ("Все панели" if lang == "ru" else "All panels"),
                                                                    "callback_data": "panels"}]]}, quiet))
    sender = _state["sender"] or _default_sender
    sent = 0
    delivered: List[str] = []
    for pid, text, markup, silent in outgoing:
        try:
            if await sender(prefs, text, markup, silent):
                sent += 1
                delivered.append(pid)
        except Exception as exc:  # noqa: BLE001 — one failed send must not stop the rest
            log.info("telegram panel notification failed: %s", type(exc).__name__)
            if pid:
                updates.pop(pid, None)

    def apply(data: Dict[str, Any]) -> None:
        for pid, row in updates.items():
            if pid in data["subs"]:
                data["sent"][pid] = row
        data["log"] = ([t for t in data["log"] if now - t < 3600] + [now] * sent)[-100:]
        if digest_due and "" in delivered:
            data["digest"]["day"] = today

    if updates or sent or digest_due:
        await asyncio.to_thread(mutate, apply)
    return sent

def listing(lang: str = "ru") -> Dict[str, Any]:
    from . import panels
    pdoc = panels.load()
    subs = load()
    rows = []
    for panel in panels.active(pdoc):
        public = panels.public_panel(panel)
        rows.append({"id": panel["id"], "kind": panel["kind"], "title": _title_and_error(panel, lang)[0],
                     "status": panel.get("status"), "eligible": eligible(panel),
                     "refreshing": bool(public.get("refreshing")),
                     "local": panel["kind"] in panels.LOCAL_KINDS,
                     "sub": subscription(panel["id"], subs), "card": card(public, lang, pdoc["settings"])})
    return {"panels": rows, "settings": subs["settings"], "modes": list(MODES)}

def request_refresh(pid: str) -> str:
    """Ask the window's scheduler to refresh soon (works from any process).

    Returns "queued", "local" (nothing to fetch) or "missing"."""
    from . import panels
    if not _PID.match(str(pid or "")):
        return "missing"
    box = {"result": "missing"}

    def apply(doc):
        for panel in panels.active(doc):
            if panel["id"] != pid:
                continue
            if panel["kind"] in panels.LOCAL_KINDS:
                box["result"] = "local"
                return
            panel["next_refresh"] = time.time()
            if panel.get("wall") and str(panel["wall"].get("status", "")).split(":")[0] in panels.USER_WALLS \
                    and panel["wall"]["status"].split(":")[0] != "needs_location":
                panel["wall"] = None
            box["result"] = "queued"

    panels.mutate(apply)
    return box["result"]

def _lang_of(prefs: Dict[str, Any]) -> str:
    return "en" if str(prefs.get("telegram_language") or "ru") == "en" else "ru"

_KIND_ICONS = {"weather": "🌤", "news": "📰", "rss": "📰", "countdown": "⏳", "todo": "✅", "currency": "💱",
               "crypto": "🪙", "stock": "📈", "sports": "🏆", "calendar": "📅", "github": "🐙", "notes": "📝",
               "system": "🖥", "usage": "📊", "clock": "🕑", "timers": "⏱", "proxy": "🛰"}
_MODE_LABELS = {"changes": ("только важное", "important only"), "every": ("любые изменения", "every change"),
                "digest": ("сводка раз в день", "daily digest")}

async def show_list(client: Any, token: str, chat_id: str, prefs: Dict[str, Any], edit_id: int = 0) -> None:
    from .telegram_bot import _io, _nav_row, _screen, _webapp_url as cached_webapp_url
    lang = _lang_of(prefs)
    ru = lang == "ru"
    try:
        data = await _io(listing, lang)
    except Exception as exc:  # noqa: BLE001
        log.info("telegram panels list failed: %s", type(exc).__name__)
        data = {"panels": []}
    rows: List[List[Dict[str, Any]]] = []
    for row in data["panels"][:20]:
        icon = _KIND_ICONS.get(row["kind"], "▫️")
        title = (row["title"] or row["kind"])[:28]
        line = [{"text": f"{icon} {title}", "callback_data": f"pnl:{row['id']}"}]
        if row["eligible"]:
            line.append({"text": "🔔" if row["sub"]["on"] else "🔕", "callback_data": f"pnlsub:{row['id']}"})
        rows.append(line)
    url = cached_webapp_url()
    if url:
        rows.append([{"text": "📱 " + ("Открыть в Mini App" if ru else "Open in Mini App"),
                      "web_app": {"url": _tab_url(url)}}])
    rows.append(_nav_row(prefs))
    if data["panels"]:
        text = ("<b>📊 " + ("Панели" if ru else "Panels") + "</b>\n" +
                ("Нажмите на панель, чтобы посмотреть её. 🔔 — присылать обновления сюда, 🔕 — не присылать."
                 if ru else "Tap a panel to view it. 🔔 — send updates here, 🔕 — don't."))
    else:
        text = ("<b>📊 " + ("Панели" if ru else "Panels") + "</b>\n" +
                ("Панелей пока нет — добавьте их в MemuAI на компьютере (главный экран → «＋ Панель»)."
                 if ru else "No panels yet — add them in MemuAI on the computer (home screen → “+ Panel”)."))
    await _screen(client, token, chat_id, text, {"inline_keyboard": rows}, edit_id, kind="panels")

async def show_panel(client: Any, token: str, chat_id: str, prefs: Dict[str, Any], pid: str, edit_id: int = 0) -> None:
    from .telegram_bot import _io, _screen
    from . import panels
    lang = _lang_of(prefs)
    ru = lang == "ru"
    doc = await _io(panels.load)
    panel = next((p for p in panels.active(doc) if p["id"] == pid), None)
    if panel is None:
        await show_list(client, token, chat_id, prefs, edit_id)
        return
    public = await _io(panels.public_panel, panel)
    text = await _io(render_html, public, lang)
    sub = await _io(subscription, pid)
    rows: List[List[Dict[str, Any]]] = [[{"text": "🔄 " + ("Обновить" if ru else "Refresh"), "callback_data": f"pnlr:{pid}"}]]
    if eligible(panel):
        rows[0].append({"text": ("🔔 " + ("Уведомления: вкл" if ru else "Updates: on")) if sub["on"]
                        else ("🔕 " + ("Уведомления: выкл" if ru else "Updates: off")), "callback_data": f"pnlsub:{pid}"})
        if sub["on"]:
            label = _MODE_LABELS[sub["mode"]][0 if ru else 1]
            rows.append([{"text": ("Присылать: " if ru else "Send: ") + label, "callback_data": f"pnlm:{pid}"},
                         {"text": ("🌙 Тихие часы: " if ru else "🌙 Quiet hours: ") +
                          (("отложить" if ru else "postpone") if sub["quiet"] == "defer" else ("без звука" if ru else "silent")),
                          "callback_data": f"pnlq:{pid}"}])
    rows.append([{"text": "← " + ("Панели" if ru else "Panels"), "callback_data": "panels"}])
    await _screen(client, token, chat_id, text, {"inline_keyboard": rows}, edit_id, kind="panel")

async def handle_callback(client: Any, token: str, chat_id: str, prefs: Dict[str, Any], data: str,
                          callback_id: str, message_id: int) -> None:
    from .telegram_bot import _io, _toast
    ru = _lang_of(prefs) == "ru"
    if data == "panels":
        await show_list(client, token, chat_id, prefs, message_id)
        return
    name, _, pid = data.partition(":")
    if not _PID.match(pid):
        await _toast(client, token, callback_id)
        return
    if name == "pnl":
        await show_panel(client, token, chat_id, prefs, pid, message_id)
    elif name == "pnlr":
        result = await _io(request_refresh, pid)
        await _toast(client, token, callback_id, {
            "queued": "🔄 Обновляю — MemuAI на компьютере обновит панель за ~20 с" if ru else "🔄 Refreshing — MemuAI on the computer updates it in ~20 s",
            "local": "Эта панель хранит свои данные — обновлять нечего" if ru else "This panel keeps its own data — nothing to fetch",
        }.get(result, "Панель не найдена" if ru else "Panel not found"))
        await show_panel(client, token, chat_id, prefs, pid, message_id)
    elif name in ("pnlsub", "pnlm", "pnlq"):
        sub = await _io(subscription, pid)
        try:
            if name == "pnlsub":
                sub = await _io(subscribe, pid, on=not sub["on"])
                await _toast(client, token, callback_id, ("🔔 Буду присылать обновления" if ru else "🔔 Updates on")
                             if sub["on"] else ("🔕 Больше не присылаю" if ru else "🔕 Updates off"))
            elif name == "pnlm":
                modes = list(MODES)
                await _io(subscribe, pid, mode=modes[(modes.index(sub["mode"]) + 1) % len(modes)])
            else:
                await _io(subscribe, pid, quiet="defer" if sub["quiet"] == "silent" else "silent")
        except ValueError:
            await _toast(client, token, callback_id, "Для этой панели уведомлений нет" if ru else "No updates for this panel")

        from .pgdata import load_telegram_state
        try:
            state = await _io(load_telegram_state)
            screen = ((state.get("sessions") or {}).get(str(chat_id)) or {}).get("menu_screen")
        except Exception:
            screen = ""
        if screen == "panels" and name == "pnlsub":
            await show_list(client, token, chat_id, prefs, message_id)
        else:
            await show_panel(client, token, chat_id, prefs, pid, message_id)
    else:
        await _toast(client, token, callback_id)

def _miniapp_lang(prefs: Dict[str, Any]) -> str:
    return "en" if prefs.get("language") == "en" else "ru"

_SUBSCRIBE_ERRORS = {"bad panel id": ("Некорректный id панели", "Invalid panel id"),
                     "panel not found": ("Панель не найдена", "Panel not found"),
                     "this panel kind has no Telegram updates": ("У панели этого вида нет обновлений в Telegram",
                                                                 "This kind of panel has no Telegram updates")}

def register_tgapp_ops(op, ApiError) -> None:  # noqa: N803 — mirrors tgapp_api names
    """Adds ``panels.*`` to the Mini App's whitelist of operations (same auth)."""

    @op("panels.list")
    async def panels_list(ctx, body):

        data = await asyncio.to_thread(listing, _miniapp_lang(ctx["prefs"]))
        data["telegram_ready"] = _can_send(ctx["prefs"])
        return data

    @op("panels.refresh")
    async def panels_refresh(ctx, body):
        result = await asyncio.to_thread(request_refresh, str(body.get("id") or ""))
        if result == "missing":
            raise ApiError(404, "not_found", "Панель не найдена", "Panel not found")
        return {"result": result}

    @op("panels.subscribe")
    async def panels_subscribe(ctx, body):
        pid = str(body.get("id") or "")
        kwargs: Dict[str, Any] = {}
        if "on" in body:
            kwargs["on"] = bool(body.get("on"))
        if body.get("mode") in MODES:
            kwargs["mode"] = body["mode"]
        if body.get("quiet") in QUIET:
            kwargs["quiet"] = body["quiet"]
        if "at" in body:
            at = str(body.get("at") or "")
            if at and not _HHMM.match(at):
                raise ApiError(400, "bad_request", "Время в формате ЧЧ:ММ", "Time as HH:MM")
            kwargs["at"] = at
        try:
            sub = await asyncio.to_thread(lambda: subscribe(pid, **kwargs))
        except ValueError as exc:
            reason = _SUBSCRIBE_ERRORS.get(str(exc), (str(exc), str(exc)))
            raise ApiError(400, "bad_request", *reason) from None
        return {"sub": sub}

def update_settings(values: Dict[str, Any]) -> Dict[str, Any]:
    def apply(data):
        merged = {**data["settings"], **{k: v for k, v in (values or {}).items() if k in DEFAULT_SETTINGS}}
        data["settings"] = _normalize({"settings": merged})["settings"]
        return data["settings"]

    _doc, settings = mutate(apply)
    return settings

def window_state() -> Dict[str, Any]:
    from .pgdata import load_preferences
    from . import panels
    doc = load()
    prefs = load_preferences()
    eligible_ids = [p["id"] for p in panels.active(panels.load()) if eligible(p)]
    return {"eligible": eligible_ids, "subs": doc["subs"], "settings": doc["settings"], "kinds": list(NOTIFY_KINDS),
            "modes": list(MODES), "ready": _can_send(prefs),
            "paired": bool(str(prefs.get("telegram_chat_id") or "").strip())}

def register_window_routes(app) -> None:
    """``/api/pg/panels-telegram`` for the panels settings in the MemuAI window."""
    from fastapi.responses import JSONResponse

    async def body_of(request: Request) -> Dict[str, Any]:
        try:
            data = await request.json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @app.get("/api/pg/panels-telegram")
    async def _tg_panels_state():
        return await asyncio.to_thread(window_state)

    @app.put("/api/pg/panels-telegram/{pid}")
    async def _tg_panels_subscribe(pid: str, request: Request):
        body = await body_of(request)
        kwargs: Dict[str, Any] = {}
        if "on" in body:
            kwargs["on"] = bool(body["on"])
        if body.get("mode") in MODES:
            kwargs["mode"] = body["mode"]
        if body.get("quiet") in QUIET:
            kwargs["quiet"] = body["quiet"]
        if "at" in body:
            if body["at"] and not _HHMM.match(str(body["at"])):
                return JSONResponse({"error": "HH:MM"}, 400)
            kwargs["at"] = str(body["at"] or "")
        try:
            return {"sub": await asyncio.to_thread(lambda: subscribe(pid, **kwargs))}
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)

    @app.put("/api/pg/panels-telegram-settings")
    async def _tg_panels_settings(request: Request):
        return {"settings": await asyncio.to_thread(update_settings, await body_of(request))}
