"""Quick Ask assistant commands — the engine (Round 22, «Jarvis без обязательного голоса»).

Layers::

    assistant_intents    phrase → action (local, instant, ru/en)
    assistant_catalog    what actions exist, tiers, parameter schemas
    assistant_commands   THIS: settings, permissions, confirmation, rate limits,
                         audit log, undo, custom commands (routines), reminders,
                         HTTP routes /api/assistant/*, model tool registration
    assistant_actions_win  Windows implementations (ctypes/COM/PowerShell)
    assistant_timers     timers / alarms / stopwatch / Pomodoro
    assistant_layout     window-layout capture/restore geometry
    assistant_apps       installed-app index and fuzzy names

Every executed (or refused) action produces a *card* — a JSON object the
Quick Ask plugin (``pgstatic/quick/plugins/commands.js``) renders: what was
done, details, an «Отменить» (undo) button when possible, or a confirmation
with a countdown for dangerous actions. Cards from the model's tool calls
and from hotkey-bound commands are queued in ``Engine.events`` and picked up
by the plugin with ``GET /api/assistant/events?after=N``.

Data (all in ``%APPDATA%/MemuAI/memuai/``, outside ``preferences.json`` on
purpose — the main window posts whole preference snapshots):
``assistant.json`` settings + custom commands + layouts,
``assistant_timers.json`` timers/Pomodoro, ``assistant_audit.jsonl`` audit log.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import math
import os
import re
import secrets
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote, quote_plus

from . import assistant_catalog as catalog
from . import assistant_intents as intents
from . import assistant_layout as geo
from .assistant_actions_win import ActionError, Backend, Unsupported, default_backend, known_folder

try:
    from fastapi import Request
except Exception:  # pragma: no cover - FastAPI is always present in the app
    Request = Any  # type: ignore

log = logging.getLogger("memuai.assistant")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "enabled": True,
    "local_match": True,
    "model_tools": True,
    "disabled_actions": [],
    "always_confirm": [],
    "trusted_actions": [],
    "confirm_countdown": 5,
    "advanced_shell": False,
    "rate_per_minute": 40,
    "model_rate_per_minute": 15,
    "search_engine": "google",
    "music_service": "youtube",
    "food_service": "",
    "shop_service": "",
    "taxi_service": "",
    "map_service": "",
    "image_model": "",
    "timer_sound": "chime",
    "timer_volume": 70,
    "timer_popup": True,
    "timer_toast": True,
    "reminder_channel": "local",
    "pomodoro": {"work": 25, "short_break": 5, "long_break": 15, "cycles": 4, "auto_breaks": True, "auto_work": False},
    "voice": {"enabled": True, "auto_speak": False, "send_on_release": True, "stt_provider": "", "stt_local_model": "",
              "stt_api_model": "", "stt_postprocess": False, "language": "", "tts_provider": "", "tts_voice": "",
              "tts_local_model": "", "tts_api_voice": "", "tts_rate": None, "hold_to_talk": True},
    "audit": True,

    "ai_router": "auto",
    "router_model": "",
    "router_timeout": 4,
}
SOUNDS = ("chime", "bell", "soft", "digital", "off")
VOICE_PROVIDERS_STT = ("", "local", "api")
VOICE_PROVIDERS_TTS = ("", "windows", "api", "local", "downloaded")
MAX_COMMANDS = 100
MAX_STEPS = 40
MAX_TRIGGERS = 12
MAX_LAYOUTS = 30
TIER_RANK = {"safe": 0, "confirm": 1, "dangerous": 2}

def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if value is None:
        return default
    return bool(value)

def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(float(value))))
    except (TypeError, ValueError):
        return default

def normalize_settings(raw: Any) -> Dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    out = json.loads(json.dumps(DEFAULT_SETTINGS))
    for key in ("enabled", "local_match", "model_tools", "advanced_shell", "timer_popup", "timer_toast", "audit"):
        out[key] = _bool(src.get(key), DEFAULT_SETTINGS[key])
    ids = set(catalog.BY_ID)
    for key in ("disabled_actions", "always_confirm", "trusted_actions"):
        value = src.get(key)
        out[key] = sorted({str(v) for v in value if str(v) in ids}) if isinstance(value, list) else []
    out["trusted_actions"] = [a for a in out["trusted_actions"] if catalog.BY_ID[a]["tier"] == "confirm"]
    out["confirm_countdown"] = _int(src.get("confirm_countdown"), 5, 0, 30)
    out["rate_per_minute"] = _int(src.get("rate_per_minute"), 40, 5, 300)
    out["model_rate_per_minute"] = _int(src.get("model_rate_per_minute"), 15, 1, 120)
    for key, allowed in (("search_engine", catalog.SEARCH_ENGINES), ("music_service", catalog.MUSIC_SERVICES)):
        out[key] = src.get(key) if src.get(key) in allowed else DEFAULT_SETTINGS[key]
    for key, allowed in (("food_service", catalog.FOOD_SERVICES), ("shop_service", catalog.SHOP_SERVICES),
                         ("taxi_service", catalog.TAXI_SERVICES), ("map_service", catalog.MAP_SERVICES)):
        out[key] = src.get(key) if src.get(key) in allowed else ""
    out["image_model"] = str(src.get("image_model") or "")[:300]
    out["timer_sound"] = src.get("timer_sound") if src.get("timer_sound") in SOUNDS else "chime"
    out["timer_volume"] = _int(src.get("timer_volume"), 70, 0, 100)
    out["reminder_channel"] = src.get("reminder_channel") if src.get("reminder_channel") in ("local", "telegram", "both") else "local"
    pomo = src.get("pomodoro") if isinstance(src.get("pomodoro"), dict) else {}
    out["pomodoro"] = {"work": _int(pomo.get("work"), 25, 1, 180), "short_break": _int(pomo.get("short_break"), 5, 1, 60),
                       "long_break": _int(pomo.get("long_break"), 15, 1, 120), "cycles": _int(pomo.get("cycles"), 4, 1, 12),
                       "auto_breaks": _bool(pomo.get("auto_breaks"), True), "auto_work": _bool(pomo.get("auto_work"), False)}
    voice = src.get("voice") if isinstance(src.get("voice"), dict) else {}
    v = out["voice"]
    for key in ("enabled", "auto_speak", "send_on_release", "stt_postprocess", "hold_to_talk"):
        v[key] = _bool(voice.get(key), DEFAULT_SETTINGS["voice"][key])
    v["stt_provider"] = voice.get("stt_provider") if voice.get("stt_provider") in VOICE_PROVIDERS_STT else ""
    v["tts_provider"] = voice.get("tts_provider") if voice.get("tts_provider") in VOICE_PROVIDERS_TTS else ""
    for key in ("stt_local_model", "stt_api_model", "tts_voice", "tts_local_model", "tts_api_voice"):
        v[key] = str(voice.get(key) or "")[:200]
    v["language"] = voice.get("language") if voice.get("language") in ("", "auto", "ru", "en") else ""
    v["tts_rate"] = _int(voice.get("tts_rate"), 0, -10, 10) if voice.get("tts_rate") not in (None, "") else None
    out["ai_router"] = src.get("ai_router") if src.get("ai_router") in ("auto", "always", "off") else "auto"
    out["router_model"] = str(src.get("router_model") or "").strip()[:300]
    out["router_timeout"] = _int(src.get("router_timeout"), 4, 1, 15)
    return out

_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

class CommandError(ValueError):
    pass

def _clean_when(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    out: Dict[str, Any] = {}
    if raw.get("app_running"):
        out["app_running"] = str(raw["app_running"])[:80]
    if raw.get("app_not_running"):
        out["app_not_running"] = str(raw["app_not_running"])[:80]
    for key in ("time_from", "time_to"):
        if raw.get(key):
            match = _HHMM.match(str(raw[key]).strip())
            if not match:
                raise CommandError(f"{key}: HH:MM")
            out[key] = f"{int(match.group(1)):02d}:{match.group(2)}"
    if isinstance(raw.get("weekdays"), list):
        days = sorted({int(d) for d in raw["weekdays"] if str(d).isdigit() and 1 <= int(d) <= 7})
        if days and len(days) < 7:
            out["weekdays"] = days
    return out or None

def validate_step(raw: Any, depth: int = 0) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise CommandError("step must be an object")
    kind = str(raw.get("type") or "action")
    step: Dict[str, Any] = {"type": kind}
    if kind == "action":
        action = str(raw.get("action") or "")
        if action not in catalog.BY_ID:
            raise CommandError(f"unknown action: {action or '—'}")
        try:
            params = catalog.validate_params(action, raw.get("params"))
        except catalog.ParamError as exc:
            raise CommandError(f"{action}: {exc}") from None
        if action == "command.run":
            params["name"] = str((raw.get("params") or {}).get("name") or params.get("name") or "")
        step.update(action=action, params=params)
    elif kind == "wait":
        try:
            seconds = float(raw.get("seconds", 1))
        except (TypeError, ValueError):
            raise CommandError("wait: seconds") from None
        step["seconds"] = max(0.1, min(600.0, seconds))
    elif kind == "ask_ai":
        prompt = str(raw.get("prompt") or "").strip()
        if not prompt:
            raise CommandError("ask_ai: prompt is empty")
        step.update(prompt=prompt[:4000], speak=_bool(raw.get("speak"), False))
    elif kind == "layout":
        try:
            step["layout"] = geo.validate(raw.get("layout"))
        except ValueError as exc:
            raise CommandError(f"layout: {exc}") from None
        step["launch"] = _bool(raw.get("launch"), True)
        step["timeout"] = _int(raw.get("timeout"), 25, 3, 180)
        step["only_listed"] = _bool(raw.get("only_listed"), False)
    elif kind in ("say", "notify"):
        text = str(raw.get("text") or "").strip()
        if not text:
            raise CommandError(f"{kind}: text is empty")
        step["text"] = text[:1000]
    else:
        raise CommandError(f"unknown step type: {kind}")
    when = _clean_when(raw.get("when"))
    if when:
        step["when"] = when
    if raw.get("continue_on_error") is not None:
        step["continue_on_error"] = _bool(raw.get("continue_on_error"), False)
    note = str(raw.get("note") or "").strip()
    if note:
        step["note"] = note[:200]
    return step

def validate_command(raw: Any, existing_ids: Optional[set] = None) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise CommandError("command must be an object")
    name = re.sub(r"\s+", " ", str(raw.get("name") or "")).strip()[:60]
    if not name:
        raise CommandError("name is empty")
    command_id = re.sub(r"[^a-z0-9_-]", "", str(raw.get("id") or "").lower())[:40] or ("cmd-" + uuid.uuid4().hex[:10])
    triggers = []
    for trig in (raw.get("triggers") or [])[:MAX_TRIGGERS * 2]:
        text = re.sub(r"\s+", " ", str(trig or "")).strip()[:120]
        if text and text.lower() not in [t.lower() for t in triggers]:
            triggers.append(text)
    triggers = triggers[:MAX_TRIGGERS]
    hotkey = str(raw.get("hotkey") or "").strip()
    if hotkey:
        from .quick_ask_core import HotkeyError, parse_hotkey
        try:
            hotkey = parse_hotkey(hotkey)[2]
        except HotkeyError as exc:
            raise CommandError(f"hotkey: {exc}") from None
    steps_raw = raw.get("steps") or []
    if not isinstance(steps_raw, list) or not steps_raw:
        raise CommandError("add at least one step")
    steps = [validate_step(step) for step in steps_raw[:MAX_STEPS]]
    now = int(time.time())
    return {"id": command_id, "name": name, "enabled": _bool(raw.get("enabled"), True), "triggers": triggers,
            "hotkey": hotkey, "trusted": _bool(raw.get("trusted"), False), "steps": steps,
            "description": str(raw.get("description") or "")[:300],
            "created": int(raw.get("created") or now) if str(raw.get("created") or "").isdigit() or isinstance(raw.get("created"), int) else now,
            "updated": now}

SAFE_OPEN_EXTENSIONS = {
    "txt", "md", "markdown", "log", "csv", "tsv", "json", "xml", "yaml", "yml", "ini", "toml", "rtf", "pdf",
    "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "epub", "djvu",
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "heic", "avif", "ico", "psd",
    "mp3", "wav", "flac", "ogg", "opus", "m4a", "aac", "wma", "mp4", "mkv", "avi", "mov", "webm", "wmv", "m4v",
    "zip", "7z", "rar", "tar", "gz",
}

def risky_path(path: str) -> bool:
    """True when opening ``path`` with its default program may run code (Round 23 hardening).

    The old check looked at the raw text: ``%COMSPEC%`` (no extension before expansion),
    ``C:\\x\\evil.exe.`` (Windows drops the trailing dot) or ``evil.hta``/``.url`` passed as «safe»."""
    value = os.path.expandvars(os.path.expanduser(str(path or "").strip().strip('"')))
    if not value:
        return True
    norm = value.replace("/", "\\")
    if norm.startswith("\\\\"):
        return True
    if ":" in norm[2:] or "%" in value:
        return True
    try:
        if os.path.isdir(value):
            return False
    except (OSError, ValueError):
        return True
    name = norm.rstrip(". ").rsplit("\\", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return ext not in SAFE_OPEN_EXTENSIONS

def hotkey_conflict(command: Dict[str, Any], others: List[Dict[str, Any]], lang: str = "ru") -> None:
    """Round 23: two commands (or a command and Quick Ask itself) on one global hotkey used to be
    saved silently — the second registration then failed with a vague «занято» in the editor."""
    hotkey = str(command.get("hotkey") or "")
    if not hotkey or not command.get("enabled", True):
        return
    for other in others:
        if other.get("enabled", True) and str(other.get("hotkey") or "").lower() == hotkey.lower():
            raise CommandError(tr(lang, f"hotkey: {hotkey} уже назначено команде «{other.get('name')}»",
                                  f"hotkey: {hotkey} is already used by “{other.get('name')}”"))
    try:
        from .pgdata import load_preferences
        from .quick_ask_core import normalize_hotkey
        quick = normalize_hotkey(load_preferences().get("quick_ask_hotkey"))
    except Exception:
        quick = ""
    if quick and quick.lower() == hotkey.lower():
        raise CommandError(tr(lang, f"hotkey: {hotkey} открывает быстрый доступ — выберите другое сочетание",
                              f"hotkey: {hotkey} opens Quick Ask — choose another combination"))

    try:
        from .quick_features import hotkey_owner
        owner = hotkey_owner(hotkey, lang, skip="command:" + str(command.get("id") or ""))
    except Exception:
        owner = ""
    if owner:
        raise CommandError(tr(lang, f"hotkey: {hotkey} уже занято в MemuAI: {owner}",
                              f"hotkey: {hotkey} is already used in MemuAI: {owner}"))

def strip_imported_paths(command: Dict[str, Any]) -> Dict[str, Any]:
    """An imported routine must not launch arbitrary executables by path (Round 23).

    A window layout restores by launching the missing programs; a captured layout stores each
    program's exe path. In a JSON file from someone else that path can be anything already on
    the disk (a download), and layout steps never asked for confirmation. After import the
    programs are found again by name/exe in the installed-apps index (Start menu, App Paths,
    Store), exactly like «открой телеграм»."""
    for step in command.get("steps") or []:
        if step.get("type") == "layout":
            for win in (step.get("layout") or {}).get("windows") or []:
                if isinstance(win.get("app"), dict):
                    win["app"]["path"] = ""
    return command

def command_tier(command: Dict[str, Any], resolve: Optional[Callable[[Any], Optional[Dict[str, Any]]]] = None,
                 *, honour_trusted: bool = True, _seen: Optional[set] = None) -> str:
    """The tier of a routine = its most dangerous step.

    Round 23: a ``command.run`` step counts with the tier of the command it runs (a «safe» routine
    could otherwise start a routine that shuts the PC down without confirmation), and an
    ``open.path`` step that may run code counts as ``confirm``."""
    seen = set(_seen or ()) | {str(command.get("id") or command.get("name") or "")}
    rank = 0
    for step in command.get("steps") or []:
        if step.get("type") != "action":
            continue
        action = step["action"]
        step_rank = TIER_RANK.get(catalog.BY_ID[action]["tier"], 0)
        params = step.get("params") or {}
        if action == "open.path" and risky_path(params.get("path", "")):
            step_rank = max(step_rank, TIER_RANK["confirm"])
        elif action == "command.run":
            ref = params.get("id") or params.get("name")
            nested = resolve(ref) if resolve and ref else None
            if nested is None or str(nested.get("id") or nested.get("name") or "") in seen:
                pass
            else:
                step_rank = max(step_rank, TIER_RANK[command_tier(nested, resolve, honour_trusted=honour_trusted,
                                                                  _seen=seen)])
        rank = max(rank, step_rank)
    tier = {0: "safe", 1: "confirm", 2: "dangerous"}[rank]
    if tier == "confirm" and command.get("trusted") and honour_trusted:
        tier = "safe"
    return tier

def template_good_morning(lang: str = "ru") -> Dict[str, Any]:
    """The example routine «Доброе утро» (the layout step is filled by «Запомнить текущие окна»)."""
    en = lang == "en"
    return {
        "name": "Good morning" if en else "Доброе утро",
        "triggers": ["good morning", "morning routine", "доброе утро", "утро", "начинаем день"],
        "hotkey": "", "enabled": True, "trusted": False,
        "description": ("Opens the work apps in their places, sets the volume and tells the weather."
                        if en else "Открывает рабочие программы на своих местах, ставит громкость и рассказывает погоду."),
        "steps": [
            {"type": "action", "action": "volume.set", "params": {"level": 35}},
            {"type": "action", "action": "theme.set", "params": {"mode": "light"}, "when": {"time_from": "07:00", "time_to": "18:00"}},
            {"type": "action", "action": "weather.get", "params": {"when": "today"}},
            {"type": "say", "text": "Good morning! Your windows are on their way." if en else "Доброе утро! Раскладываю окна."},
        ],
    }

def _memu_dir() -> Path:
    from .pgdata import MEMU_DIR
    return Path(MEMU_DIR)

class Store:
    """``assistant.json``: {version, settings, commands[], layouts{}}."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else None
        self.lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path or (_memu_dir() / "assistant.json")

    def _read(self) -> Dict[str, Any]:
        try:
            from .storage import load_json_with_recovery
            data = load_json_with_recovery(str(self.path), {}, validator=lambda v: isinstance(v, dict))
        except Exception:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        return data if isinstance(data, dict) else {}

    def load(self) -> Dict[str, Any]:
        with self.lock:
            data = self._read()
            commands = []
            for row in data.get("commands") or []:
                try:
                    clean = validate_command(row)
                    clean["created"] = row.get("created", clean["created"]) if isinstance(row, dict) else clean["created"]
                    clean["updated"] = row.get("updated", clean["updated"]) if isinstance(row, dict) else clean["updated"]
                    commands.append(clean)
                except CommandError:
                    continue
            layouts = {}
            for name, layout in (data.get("layouts") or {}).items() if isinstance(data.get("layouts"), dict) else []:
                try:
                    layouts[str(name)[:60]] = geo.validate(layout)
                except ValueError:
                    continue
            return {"version": 1, "settings": normalize_settings(data.get("settings")), "commands": commands[:MAX_COMMANDS],
                    "layouts": dict(list(layouts.items())[:MAX_LAYOUTS])}

    def save(self, data: Dict[str, Any]) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            from .storage import save_json
            save_json(str(self.path), data, backup=True)

    def update(self, fn: Callable[[Dict[str, Any]], Any]) -> Dict[str, Any]:
        with self.lock:
            data = self.load()
            fn(data)
            self.save(data)
            return data

class Audit:
    def __init__(self, path: Optional[Path] = None, limit: int = 2000) -> None:
        self._path = Path(path) if path else None
        self.limit = limit
        self.lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path or (_memu_dir() / "assistant_audit.jsonl")

    def write(self, row: Dict[str, Any]) -> None:
        line = json.dumps(row, ensure_ascii=False)[:4000]
        with self.lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                if self.path.stat().st_size > 1_500_000:
                    rows = self.path.read_text(encoding="utf-8").splitlines()[-self.limit:]
                    self.path.write_text("\n".join(rows) + "\n", encoding="utf-8")
            except OSError as exc:
                log.debug("audit not written: %s", exc)

    def read(self, limit: int = 200) -> List[Dict[str, Any]]:
        try:
            rows = self.path.read_text(encoding="utf-8").splitlines()[-limit:]
        except OSError:
            return []
        out = []
        for row in reversed(rows):
            try:
                out.append(json.loads(row))
            except ValueError:
                continue
        return out

    def clear(self) -> None:
        with self.lock:
            try:
                self.path.unlink()
            except OSError:
                pass

def _q(text: str) -> str:
    return quote_plus(str(text or "").strip())

def search_url(query: str, engine: str = "google") -> str:
    return {"google": "https://www.google.com/search?q=", "yandex": "https://yandex.ru/search/?text=",
            "duckduckgo": "https://duckduckgo.com/?q=", "bing": "https://www.bing.com/search?q="}.get(
        engine, "https://www.google.com/search?q=") + _q(query)

def media_url(query: str, service: str = "youtube") -> str:
    return {"youtube": "https://www.youtube.com/results?search_query=" + _q(query),
            "youtube_music": "https://music.youtube.com/search?q=" + _q(query),
            "yandex_music": "https://music.yandex.ru/search?text=" + _q(query),
            "spotify": "https://open.spotify.com/search/" + quote(str(query or "").strip()),
            "vk": "https://vk.com/audio?q=" + _q(query),
            "soundcloud": "https://soundcloud.com/search?q=" + _q(query)}.get(service) or \
        "https://www.youtube.com/results?search_query=" + _q(query)

def food_url(query: str, service: str) -> str:
    return {"yandex_eda": "https://eda.yandex.ru/search?query=" + _q(query),
            "delivery_club": "https://eda.yandex.ru/search?query=" + _q(query),
            "samokat": "https://samokat.ru/search?value=" + _q(query),
            "kuper": "https://kuper.ru/multisearch?q=" + _q(query),
            "ubereats": "https://www.ubereats.com/search?q=" + _q(query),
            "doordash": "https://www.doordash.com/search/store/" + quote(str(query or "").strip()) + "/",
            "deliveroo": "https://deliveroo.co.uk/search?q=" + _q(query),
            "wolt": "https://wolt.com/search?q=" + _q(query)}.get(service, "https://eda.yandex.ru/search?query=" + _q(query))

def shop_url(query: str, service: str) -> str:
    return {"ozon": "https://www.ozon.ru/search/?text=" + _q(query),
            "wildberries": "https://www.wildberries.ru/catalog/0/search.aspx?search=" + _q(query),
            "yandex_market": "https://market.yandex.ru/search?text=" + _q(query),
            "amazon": "https://www.amazon.com/s?k=" + _q(query),
            "aliexpress": "https://www.aliexpress.com/w/wholesale-" + quote(str(query or "").strip().replace(" ", "-")) + ".html",
            "avito": "https://www.avito.ru/rossiya?q=" + _q(query),
            "ebay": "https://www.ebay.com/sch/i.html?_nkw=" + _q(query)}.get(service, "https://www.ozon.ru/search/?text=" + _q(query))

def taxi_url(to: str, service: str) -> str:
    if service == "uber":
        return "https://m.uber.com/looking" + (("?drop[0]=" + quote(json.dumps({"addressLine1": to}, ensure_ascii=False))) if to else "")
    if service == "bolt":
        return "https://bolt.eu/"
    return "https://go.yandex/" + (("?text=" + _q(to)) if to else "")

def map_url(to: str, start: str, service: str) -> str:
    if service == "google_maps":
        if start:
            return f"https://www.google.com/maps/dir/?api=1&origin={_q(start)}&destination={_q(to)}"
        return "https://www.google.com/maps/search/?api=1&query=" + _q(to)
    if service == "2gis":
        return "https://2gis.ru/search/" + quote(to)
    if start:
        return f"https://yandex.ru/maps/?rtext={_q(start)}~{_q(to)}"
    return "https://yandex.ru/maps/?text=" + _q(to)

def normalize_url(text: str) -> str:
    """``github.com`` → ``https://github.com``; only http(s); raises ActionError otherwise."""
    value = str(text or "").strip()
    low = value.lower()
    if low in intents.KNOWN_SITES:
        return intents.KNOWN_SITES[low]
    if re.match(r"^[a-z][a-z0-9+.-]*:", low) and not low.startswith(("http://", "https://")):
        raise ActionError("Открываю только ссылки http(s)", "bad_params", "I only open http(s) links")
    if not low.startswith(("http://", "https://")):
        value = "https://" + value
    if not re.match(r"^https?://[^\s/$.?#].[^\s]*$", value, re.I):
        raise ActionError("Это не похоже на адрес сайта", "bad_params", "This doesn't look like a website address")
    return value

def default_service(kind: str, settings: Dict[str, Any], lang: str) -> str:
    chosen = settings.get(f"{kind}_service") or ""
    if chosen:
        return chosen
    ru = lang != "en"
    return {"food": "yandex_eda" if ru else "ubereats", "shop": "ozon" if ru else "amazon",
            "taxi": "yandex_go" if ru else "uber", "map": "yandex_maps" if ru else "google_maps"}[kind]

_SETTINGS_LABELS = {
    "sound": ("Звук", "Sound"), "display": ("Экран", "Display"), "bluetooth": ("Bluetooth", "Bluetooth"),
    "wifi": ("Wi‑Fi", "Wi‑Fi"), "network": ("Сеть и интернет", "Network & internet"), "update": ("Центр обновления", "Windows Update"),
    "apps": ("Приложения", "Apps"), "startup": ("Автозагрузка", "Startup"), "battery": ("Батарея", "Battery"),
    "power": ("Питание и спящий режим", "Power & sleep"), "mouse": ("Мышь", "Mouse"), "keyboard": ("Клавиатура", "Keyboard"),
    "personalization": ("Персонализация", "Personalization"), "background": ("Фон", "Background"), "colors": ("Цвета", "Colors"),
    "themes": ("Темы", "Themes"), "taskbar": ("Панель задач", "Taskbar"), "notifications": ("Уведомления", "Notifications"),
    "privacy": ("Конфиденциальность", "Privacy"), "camera": ("Камера", "Camera"), "microphone": ("Микрофон", "Microphone"),
    "printers": ("Принтеры", "Printers"), "datetime": ("Дата и время", "Date & time"), "language": ("Язык и регион", "Language & region"),
    "storage": ("Память", "Storage"), "about": ("О системе", "About"), "nightlight": ("Ночной свет", "Night light"),
    "vpn": ("VPN", "VPN"), "airplane": ("Режим «в самолёте»", "Airplane mode"), "clipboard": ("Буфер обмена", "Clipboard"),
    "accessibility": ("Специальные возможности", "Accessibility"), "default_apps": ("Приложения по умолчанию", "Default apps"),
    "lockscreen": ("Экран блокировки", "Lock screen"),
}

def _param_error_ru(text: str) -> str:
    """Round 34: the validator's English («missing: text», «level: expected a number») on a Russian card."""
    for pattern, ru in ((r"^missing: (.+)$", "не хватает {0}"), (r"^(\w+): expected text$", "{0} — нужен текст"),
                        (r"^(\w+): expected a (?:finite )?number$", "{0} — нужно число"),
                        (r"^(\w+): expected true/false$", "{0} — нужно да/нет"), (r"^(\w+): one of (.+)$", "{0} — одно из: {1}")):
        found = re.match(pattern, text)
        if found:
            return ru.format(*found.groups())
    return text

def parse_when(params: Dict[str, Any], now: datetime) -> datetime:
    """``at`` (ISO / HH:MM) or ``in_minutes`` → local datetime in the future. Raises ActionError."""
    if params.get("in_minutes") not in (None, ""):
        minutes = float(params["in_minutes"])
        if minutes <= 0:
            raise ActionError("Укажите время в будущем", "bad_params", "Give a time in the future")
        return now + timedelta(minutes=minutes)
    at = str(params.get("at") or "").strip()
    if not at:
        raise ActionError("Когда напомнить? Укажите время, например «в 15:00» или «через 20 минут».", "need_time",
                          "When should I remind you? Give a time, e.g. “at 15:00” or “in 20 minutes”.")
    match = _HHMM.match(at)
    if match:
        when = now.replace(hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0)
        if when <= now:
            when += timedelta(days=1)
        return when
    try:
        when = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        found = intents.find_clock(at, now)
        if not found:
            raise ActionError("Не понял время напоминания", "bad_params",
                              "I didn't understand the reminder time") from None
        when = found[0]
    if when.tzinfo is not None:
        when = when.astimezone().replace(tzinfo=None)
    if when <= now - timedelta(seconds=30):
        raise ActionError("Это время уже прошло", "past", "That time has already passed")
    return when

def human_bytes(value: Optional[float], lang: str = "ru") -> str:
    if value is None:
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"] if lang == "en" else ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit not in ("B", "Б") else f"{int(size)} {unit}"
        size /= 1024
    return str(value)

_REASONING = re.compile(r"<(think|thinking|reasoning|ds_safety)>.*?(?:</\1>|$)|◁think▷.*?(?:◁/think▷|$)", re.S | re.I)

def clean_ai_text(text: Any) -> str:
    """A model answer without its reasoning: the Proxy streams it in ``<think>…</think>`` (``think_tags``), some
    models inline it, DeepSeek adds ``<ds_safety>…</ds_safety>``. A stray closing tag cuts everything before it."""
    out = _REASONING.sub("", str(text or ""))
    for tag in ("</think>", "</thinking>", "</reasoning>", "◁/think▷"):
        if tag in out:
            out = out.rsplit(tag, 1)[1]

    from .answer_guard import clean as _guard_clean
    return _guard_clean(out).strip()

def tr(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru

def _finite(value: Any, default: float) -> Optional[float]:
    """A finite float from a request field (``None`` for text, NaN or ±inf)."""
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None

class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.hits: Dict[str, Deque[float]] = collections.defaultdict(collections.deque)

    def allow(self, key: str, limit: int, window: float = 60.0) -> bool:
        now = self.clock()
        bucket = self.hits[key]
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True

DANGEROUS_PER_MINUTE = 3

MODEL_CONFIRM = {"clipboard.get"}

MODEL_CLIPBOARD_TEXT = {"text.translate", "text.summarize", "text.encode", "json.format", "hash.text", "text.case", "text.stats"}

class Engine:
    def __init__(self, *, backend: Optional[Backend] = None, store: Optional[Store] = None,
                 audit: Optional[Audit] = None, timers=None, clock: Callable[[], float] = time.time,
                 now_fn: Callable[[], datetime] = datetime.now) -> None:
        self.backend = backend or default_backend()
        self.store = store or Store()
        self.audit = audit or Audit()
        self.clock = clock
        self.now_fn = now_fn
        self.rate = RateLimiter()
        self._timers = timers
        self.pending: Dict[str, Dict[str, Any]] = {}
        self.undo_stack: "collections.OrderedDict[str, Dict[str, Any]]" = collections.OrderedDict()
        self.events: Deque[Dict[str, Any]] = collections.deque(maxlen=200)
        self.seq = 0
        self.lock = threading.RLock()

        self.completion: Optional[Callable[..., Awaitable[str]]] = None
        self.proxy_sse: Optional[Callable[[str, bytes], Any]] = None
        self.proxy_json: Optional[Callable[..., Awaitable[Any]]] = None
        self.quick_manager: Callable[[], Any] = lambda: None
        self.app_state = None
        self.ringer = None
        self.reminder_store = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.handlers: Dict[str, Callable[..., Awaitable[Dict[str, Any]]]] = {}
        self._install_handlers()
        try:
            from . import assistant_r24
            assistant_r24.bind(self)
        except Exception as exc:
            log.warning("round 24 assistant actions not bound: %s", exc)
        try:
            from . import assistant_r25
            assistant_r25.bind(self)
        except Exception as exc:
            log.warning("round 25 assistant actions not bound: %s", exc)

    @property
    def timers(self):
        if self._timers is None:
            from .assistant_timers import TimerManager
            self._timers = TimerManager(_memu_dir() / "assistant_timers.json", on_fire=self._on_ring)
            self._timers.start()
        return self._timers

    def settings(self) -> Dict[str, Any]:
        return self.store.load()["settings"]

    def emit(self, card: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            self.seq += 1
            card["seq"] = self.seq
            self.events.append(card)
        return card

    def events_after(self, seq: int) -> List[Dict[str, Any]]:
        with self.lock:
            return [dict(card) for card in self.events if card.get("seq", 0) > seq]

    async def _blocking(self, fn: Callable, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    def _card(self, action: str, lang: str, status: str, message: str = "", **extra: Any) -> Dict[str, Any]:
        card = {"id": uuid.uuid4().hex[:10], "action": action, "title": catalog.title(action, lang)
                if action in catalog.BY_ID else extra.pop("title", action), "status": status, "message": message,
                "ts": self.clock(), "tier": (catalog.BY_ID.get(action) or {}).get("tier", "safe")}
        card.update({k: v for k, v in extra.items() if v not in (None, [], {}, "")})
        return card

    async def run(self, action: str, params: Any = None, *, source: str = "local", lang: str = "ru",
                  confirmed: bool = False, depth: int = 0, emit: bool = None) -> Dict[str, Any]:
        """Validate, check permissions, confirm, execute and audit one action. Always returns a card."""
        emit = (source in ("model", "hotkey", "routine-bg")) if emit is None else emit
        lang = "en" if lang == "en" else "ru"
        settings = self.settings()
        row = catalog.get(action)
        if not row and not action.startswith("_"):
            return self._finish(self._card(action, lang, "error", tr(lang, "Неизвестное действие", "Unknown action"),
                                           code="unknown"), source, params, emit)
        if action.startswith("_") and source != "undo":
            return self._card(action, lang, "error", "internal action", code="unknown")
        if not settings["enabled"] and source != "undo":
            return self._finish(self._card(action, lang, "disabled", tr(
                lang, "Команды ассистента выключены в настройках быстрого доступа.",
                "Assistant commands are turned off in Quick Ask settings."), code="disabled"), source, params, emit)
        if row and action in settings["disabled_actions"]:
            return self._finish(self._card(action, lang, "disabled", tr(
                lang, "Это действие выключено в настройках быстрого доступа.",
                "This action is turned off in Quick Ask settings."), code="disabled"), source, params, emit)
        if row and row["advanced"] and not settings["advanced_shell"]:
            return self._finish(self._card(action, lang, "disabled", tr(
                lang, "Выполнение команд PowerShell выключено (Настройки → Быстрый доступ → Команды → «Расширенный режим»).",
                "PowerShell commands are off (Settings → Quick Ask → Commands → “Advanced mode”)."), code="advanced_off"),
                source, params, emit)
        if row and action == "command.run" and isinstance(params, dict) and params.get("id") and not params.get("name"):

            found = self._find_command(params.get("id"))
            params = dict(params, name=str((found or {}).get("name") or params.get("id")))
        try:
            clean = catalog.validate_params(action, params) if row else dict(params or {})
        except catalog.ParamError as exc:
            return self._finish(self._card(action, lang, "error", tr(lang, "Неверные параметры: ", "Bad parameters: ")
                                           + (_param_error_ru(str(exc)) if lang != "en" else str(exc)),
                                           code="bad_params"), source, params, emit)
        if row and action == "command.run":
            clean["id"] = str((params or {}).get("id") or "")
        if row and action == "schedule.plan" and confirmed:

            clean["draft"] = str((params or {}).get("draft") or "")[:64]

        if source != "undo":
            limit_key = "model" if source in ("model", "router") else "all"
            limit = settings["model_rate_per_minute"] if source in ("model", "router") else settings["rate_per_minute"]
            if not self.rate.allow(limit_key, limit) or (row and row["tier"] == "dangerous" and confirmed
                                                         and not self.rate.allow("dangerous", DANGEROUS_PER_MINUTE)):
                return self._finish(self._card(action, lang, "error", tr(
                    lang, "Слишком много команд подряд — подождите минуту.", "Too many commands in a row — wait a minute."),
                    code="rate_limited"), source, clean, emit)
        tier = row["tier"] if row else "safe"
        if action == "command.run":
            command = self._find_command(clean.get("id") or clean.get("name"))
            if command:

                tier = command_tier(command, self._find_command, honour_trusted=source not in ("model", "router"))
        if action == "open.path" and risky_path(str(clean.get("path", ""))):
            tier = "confirm"
        if tier == "safe" and action in settings["always_confirm"]:
            tier = "confirm"
        if tier == "safe" and source in ("model", "router") and (action in MODEL_CONFIRM or await self._model_reads_clipboard(action, clean)):
            tier = "confirm"
        if tier == "confirm" and action in settings["trusted_actions"] and source not in ("model", "router"):
            tier = "safe"
        if action == "process.kill" and not confirmed:

            from .assistant_actions_win import PROTECTED_PROCESSES
            name = str(clean.get("name") or "").strip().lower()
            if (clean.get("pid") in (0, 4, os.getpid())
                    or (name and (name in PROTECTED_PROCESSES or name + ".exe" in PROTECTED_PROCESSES))):
                return self._finish(self._card(action, lang, "error", tr(
                    lang, "Это системный процесс или сам MemuAI — его не завершаю.",
                    "This is a system process or MemuAI itself — not ending it."), code="blocked"), source, clean, emit)
        if tier != "safe" and not confirmed:
            hint = await self._confirm_hint(action, clean, lang)
            return self._ask_confirmation(action, clean, tier, source, lang, settings, emit, summary=hint)
        handler = self.handlers.get(action)
        if handler is None:
            return self._finish(self._card(action, lang, "error", "no handler", code="unknown"), source, clean, emit)
        started = time.monotonic()
        try:
            result = await handler(clean, lang=lang, source=source, depth=depth)
            card = self._card(action, lang, result.pop("status", "done"), result.pop("message", ""), **{
                k: v for k, v in result.items() if k != "undo"})
            undo = result.get("undo")
            if undo:
                card["undo"] = self._remember_undo(undo, card, lang)
        except Unsupported as exc:
            card = self._card(action, lang, "unsupported", exc.text(lang) if lang != "en" or exc.en else
                              "This action works only in the MemuAI Windows app.", code="unsupported")
        except ActionError as exc:
            card = self._card(action, lang, "error", exc.text(lang), code=exc.code)
        except Exception as exc:
            log.exception("assistant action %s failed", action)
            card = self._card(action, lang, "error", f"{type(exc).__name__}: {exc}"[:400], code="failed")
        card["ms"] = int((time.monotonic() - started) * 1000)
        return self._finish(card, source, clean, emit)

    async def _model_reads_clipboard(self, action: str, params: Dict[str, Any]) -> bool:
        if action not in MODEL_CLIPBOARD_TEXT or str(params.get("text") or "").strip():
            return False
        if action == "text.summarize":
            return await self.open_page() is None
        return True

    async def open_page(self) -> Optional[Dict[str, Any]]:
        """The browser tab Quick Ask works with: ``QuickAskManager.current_page()`` (the tab on screen NOW — the one
        remembered at the hotkey goes stale) or the remembered ``page``; ``None`` without a browser tab."""
        manager = self.quick_manager()
        if manager is None:
            return None
        try:
            fresh = getattr(manager, "current_page", None)
            page = await self._blocking(fresh) if callable(fresh) else getattr(manager, "page", None)
        except Exception:
            page = getattr(manager, "page", None)
        return page if isinstance(page, dict) and (page.get("url") or page.get("hwnd")) else None

    def _finish(self, card: Dict[str, Any], source: str, params: Any, emit: bool) -> Dict[str, Any]:
        card["source"] = source
        try:
            if self.settings().get("audit", True):
                self.audit.write({"ts": round(card.get("ts", self.clock()), 2), "action": card.get("action"),
                                  "status": card.get("status"), "source": source,
                                  "params": _audit_params(card.get("action", ""), params),

                                  "message": "" if card.get("action") in _private_actions() else str(card.get("message") or "")[:300]})
        except Exception:
            pass
        if emit:
            self.emit(card)
        return card

    def _ask_confirmation(self, action: str, params: Dict[str, Any], tier: str, source: str, lang: str,
                          settings: Dict[str, Any], emit: bool, summary: str = "") -> Dict[str, Any]:
        token = secrets.token_urlsafe(12)
        countdown = settings["confirm_countdown"] if tier == "dangerous" else 0
        now = self.clock()
        with self.lock:
            for key in [k for k, v in self.pending.items() if v["expires"] < now]:
                self.pending.pop(key, None)
            self.pending[token] = {"action": action, "params": params, "source": source, "lang": lang,
                                   "expires": now + 120, "tier": tier}
        summary = summary or describe(action, params, lang, self)
        message = summary if action in CONFIRM_TEXT or action in ("app.close", "process.kill", "type.text", "keys.press",
                                                                   "wifi.set", "shell.run", "open.path", "computer.use") \
            else tr(lang, "Подтвердите: ", "Confirm: ") + summary
        card = self._card(action, lang, "confirm", message, confirm={"token": token, "tier": tier, "countdown": countdown,
                                                                     "expires": now + 120}, summary=summary)
        return self._finish(card, source, params, emit)

    async def confirm(self, token: str, accept: bool, lang: str = "ru") -> Dict[str, Any]:
        with self.lock:
            request = self.pending.pop(str(token or ""), None)
        if not request or request["expires"] < self.clock():
            return self._card("assistant.help", lang, "error", tr(lang, "Подтверждение устарело — повторите команду.",
                                                                  "This confirmation expired — repeat the command."),
                              code="expired", title=tr(lang, "Подтверждение", "Confirmation"))
        if not accept:
            card = self._card(request["action"], request["lang"], "cancelled", tr(request["lang"], "Отменено.", "Cancelled."))
            return self._finish(card, request["source"], request["params"], request["source"] == "model")
        return await self.run(request["action"], request["params"], source=request["source"], lang=request["lang"],
                              confirmed=True, emit=request["source"] in ("model", "hotkey"))

    def _remember_undo(self, undo: Dict[str, Any], card: Dict[str, Any], lang: str) -> str:
        undo_id = uuid.uuid4().hex[:10]
        with self.lock:
            self.undo_stack[undo_id] = {**undo, "title": card.get("title"), "ts": self.clock(), "lang": lang}
            while len(self.undo_stack) > 30:
                self.undo_stack.popitem(last=False)
        return undo_id

    async def undo(self, undo_id: str = "", lang: str = "ru") -> Dict[str, Any]:
        with self.lock:
            if not undo_id and self.undo_stack:
                undo_id = next(reversed(self.undo_stack))
            spec = self.undo_stack.pop(undo_id, None) if undo_id else None
        if not spec:
            return self._card("assistant.undo", lang, "error", tr(lang, "Нечего отменять.", "Nothing to undo."), code="empty")
        card = await self.run(spec["action"], spec.get("params") or {}, source="undo", lang=lang, confirmed=True)
        card["title"] = tr(lang, "Отменено: ", "Undone: ") + str(spec.get("title") or "")
        if card["status"] == "done":
            card["message"] = card.get("message") or tr(lang, "Вернул как было.", "Reverted.")
        return card

    def _find_command(self, ref: Any) -> Optional[Dict[str, Any]]:
        ref = str(ref or "").strip()
        if not ref:
            return None
        commands = self.store.load()["commands"]
        for command in commands:
            if command["id"] == ref:
                return command
        low = ref.lower()
        for command in commands:
            if command["name"].lower() == low:
                return command
        hit = intents.match_custom(ref, commands, threshold=0.8)
        return hit["command"] if hit else None

    def match(self, text: str, lang: str = "ru", conversation: bool = False) -> Optional[Dict[str, Any]]:
        data = self.store.load()
        settings = data["settings"]
        if not settings["enabled"] or not settings["local_match"]:
            return None
        found = intents.match(text, now=self.now_fn(), commands=data["commands"], disabled=settings["disabled_actions"],
                              conversation=conversation)
        if not found:
            return None
        row = catalog.get(found["action"])
        if row and row["advanced"]:
            return None
        found["title"] = catalog.title(found["action"], lang)
        found["tier"] = row["tier"] if row else "safe"
        return found

    async def precheck(self, found: Dict[str, Any]) -> bool:
        """Round 23: is a locally matched «закрой X» / «выключи X» about a running app at all?

        ``app.close`` is a confirm-tier action, so the confirmation card used to appear before the
        handler could notice there is no such window («закрой вопрос», «выключи музыку» → «Закрыть
        вопрос?»). ``False`` sends the phrase to the model instead. Unknown (no window list on this
        OS) counts as «yes» so the honest «only in Windows» card still appears."""
        params = found.get("params") or {}
        if found.get("action") == "window.action" and params.get("target"):
            try:
                return bool(await self._blocking(self._windows_of, str(params["target"])))
            except Exception:
                return True
        if found.get("action") not in ("app.close", "app.switch"):
            return True
        name = str(params.get("name") or "")
        try:

            targets = await self._blocking(self._app_targets, name)
            return bool(targets["windows"] or targets["procs"])
        except (Unsupported, ActionError):
            return True
        except Exception:
            return True

    def _on_ring(self, event: Dict[str, Any]) -> None:
        settings = self.settings()
        lang = self._lang()
        label = event.get("label") or ""
        kind = event.get("kind")
        if kind == "pomodoro":
            title = {"work": tr(lang, "Pomodoro: перерыв", "Pomodoro: break time"),
                     "short_break": tr(lang, "Pomodoro: за работу", "Pomodoro: back to work"),
                     "long_break": tr(lang, "Pomodoro: за работу", "Pomodoro: back to work")}.get(event.get("ended"), "Pomodoro")
            text = tr(lang, "Следующая фаза: ", "Next: ") + phase_name(event.get("next"), lang)
        elif kind == "reminder":
            title = tr(lang, "Напоминание", "Reminder")
            text = event.get("text") or label
        elif kind == "alarm":
            title = tr(lang, "Будильник", "Alarm") + (f" · {label}" if label else "")
            text = datetime.fromtimestamp(event.get("at") or time.time()).strftime("%H:%M")
        else:
            title = tr(lang, "Таймер", "Timer") + (f" · {label}" if label else "")
            text = tr(lang, "Время вышло", "Time is up") + (f" ({intents.duration_text(int(event.get('duration') or 0), lang)})"
                                                             if event.get("duration") else "")
        event["title"], event["message"] = title, text
        self.emit({"id": event["id"], "action": "timer.ring", "title": title, "message": text, "status": "ringing",
                   "kind": kind, "ts": self.clock(), "ring": event})
        if settings["timer_sound"] != "off":
            try:
                if self.ringer is None:
                    from .assistant_timers import Ringer
                    self.ringer = Ringer()
                seconds = 4 if kind == "pomodoro" else 60
                self.ringer.ring(settings["timer_sound"], settings["timer_volume"], seconds)
            except Exception as exc:
                log.debug("ring failed: %s", exc)
        if settings["timer_toast"]:
            try:
                from .desktop_notifications import show_completion
                show_completion(f"MemuAI · {title}", text, sound=False)
            except Exception:
                pass
        if settings["timer_popup"]:
            manager = self.quick_manager()
            if manager is not None:
                try:
                    manager.show("timer")
                except Exception as exc:
                    log.debug("quick popup failed: %s", exc)

    def stop_ring(self) -> None:
        if self.ringer is not None:
            self.ringer.stop()

    def _lang(self) -> str:
        try:
            from .pgdata import load_preferences
            return "en" if load_preferences().get("language") == "en" else "ru"
        except Exception:
            return "ru"

    def _install_handlers(self) -> None:
        h = self.handlers
        for name in dir(self):
            if name.startswith("h_"):
                action = name[2:].replace("__", ".")
                if action.startswith("_"):
                    action = "_" + action[1:]
                h[action] = getattr(self, name)
        try:
            from . import schedule_quick
            schedule_quick.register(self)
        except Exception:
            log.exception("schedule.plan handler")

    def _resolve_app(self, name: str) -> Dict[str, Any]:
        from . import assistant_apps
        index = self.backend.app_index()
        return assistant_apps.resolve(name, index)

    async def h_app__open(self, p, *, lang, **_):
        name = p["name"]
        low = name.lower().strip()
        found = await self._blocking(self._resolve_app, name)
        entry = found["match"]
        if entry is None:
            if low in intents.KNOWN_SITES or re.match(r"^[\w.-]+\.[a-z]{2,}$", low):
                return await self.h_open__url({"url": name}, lang=lang)
            alternatives = [a["name"] for a in found["alternatives"][:4]]
            raise ActionError(tr(lang, f"Не нашёл приложение «{name}».", f"Couldn't find the app “{name}”.") +
                              ((tr(lang, " Похожие: ", " Similar: ") + ", ".join(alternatives)) if alternatives else ""),
                              "not_found")
        await self._blocking(self.backend.launch_app, entry)
        return {"message": tr(lang, f"Открываю {entry['name']}", f"Opening {entry['name']}"),
                "details": [[tr(lang, "Источник", "Source"), {"lnk": tr(lang, "меню «Пуск»", "Start menu"), "uwp": "Microsoft Store / Start",
                                                              "apppath": "App Paths", "builtin": "Windows"}.get(entry["kind"], entry["kind"])]],
                "data": {"app": entry["name"], "score": found["score"]}}

    def _windows_of(self, name: str) -> List[Dict[str, Any]]:
        from . import assistant_apps
        rows = self.backend.windows()
        scored = []
        for w in rows:
            if w.get("own"):
                continue
            score = assistant_apps.match_process(name, w.get("exe", ""), w.get("title", ""))

            from .assistant_procs_win import exe_folder
            folder = exe_folder(w.get("path") or "")
            if folder and score < 0.9:
                score = max(score, assistant_apps.match_process(name, folder, "") - 0.01)
            scored.append((score, w))
        best = max((s for s, _w in scored), default=0)
        if best < 0.8:
            return []
        return [w for s, w in scored if s >= best - 0.02]

    def _background_procs(self, name: str) -> List[Dict[str, Any]]:
        """Round 33 (r33-quick): processes of an app that has NO visible window — it lives in the tray («закрой incy»).

        Only when the backend can list processes (Windows: assistant_procs_win); Windows' own programs, hosts and
        MemuAI are never candidates, and the bar is higher than for windows (0.9) — a wrong process is worse."""
        from . import assistant_apps
        from .assistant_procs_win import exe_folder, user_apps
        try:
            rows = self.backend.procs()
        except Exception:
            return []
        scored = []
        for row in user_apps(rows, own_pid=self.backend.own_pid()):
            score = assistant_apps.match_process(name, row.get("exe", ""), "")
            folder = exe_folder(row.get("path") or "")
            if folder and score < 0.9:
                score = max(score, assistant_apps.match_process(name, folder, "") - 0.01)
            scored.append((score, row))
        best = max((s for s, _r in scored), default=0)
        if best < 0.9:
            return []
        return [r for s, r in scored if s >= best - 0.02][:12]

    def _app_targets(self, name: str) -> Dict[str, Any]:
        """``{windows, procs, label}`` of a running app: its visible windows, else its windowless (tray) processes."""
        from .assistant_procs_win import app_label
        wins = self._windows_of(name)
        procs = [] if wins else self._background_procs(name)
        if wins:
            from .assistant_procs_win import pretty_label
            label = pretty_label(wins[0].get("app") or name, wins[0].get("title") or "")
        elif procs:
            label = app_label(procs[0].get("name") or procs[0].get("exe", ""), procs[0].get("path", ""))
        else:
            label = name
        return {"windows": wins, "procs": procs, "label": label}

    def _wait_exit(self, pids: Iterable[int], seconds: float) -> Optional[bool]:
        """True when every pid ended within ``seconds``, False when one still runs, None when unknown (no API)."""
        pids = [int(p) for p in pids if p]
        if not pids:
            return None
        for step in range(int(max(0.0, seconds) / 0.15) + 1):
            try:
                alive = [p for p in pids if self.backend.pid_alive(p)]
            except Exception:
                return None
            if not alive:
                return True
            if step * 0.15 >= seconds:
                break
            time.sleep(0.15)
        return False

    async def _confirm_hint(self, action: str, params: Dict[str, Any], lang: str) -> str:
        """Round 33: the confirmation line of «закрой X» names the real app and says when it lives in the tray."""
        if action != "app.close":
            return ""
        try:
            found = await asyncio.wait_for(self._blocking(self._app_targets, str(params.get("name") or "")), 3)
        except Exception:
            return ""
        label = found["label"]
        if found["windows"]:
            return tr(lang, f"Закрыть {label}? О несохранённых изменениях программа спросит сама.",
                      f"Close {label}? The app itself asks about unsaved changes.")
        if found["procs"]:
            return tr(lang, f"{label} работает в фоне (в области уведомлений), окна нет. Завершить программу? "
                            "Сначала попрошу её закрыться сама; если не закроется — завершу процесс.",
                      f"{label} runs in the background (notification area) without a window. Quit it? "
                      "I'll ask it to close first and end the process only if it doesn't.")
        return ""

    async def h_app__close(self, p, *, lang, **_):
        found = await self._blocking(self._app_targets, p["name"])
        wins, procs, label = found["windows"], found["procs"], found["label"]
        if not wins and not procs:
            raise ActionError(tr(lang, f"«{p['name']}» не запущено.", f"“{p['name']}” is not running."), "not_running")
        if wins:
            for win in wins:
                await self._blocking(self.backend.close_window, win["hwnd"])
            pids = sorted({int(w.get("pid") or 0) for w in wins} - {0})
            gone = await self._blocking(self._wait_exit, pids, 2.0)
            if gone is False:
                try:
                    still = [w for w in await self._blocking(self.backend.windows) if int(w.get("pid") or 0) in pids]
                except Exception:
                    still = []
                if not still:

                    return {"message": tr(lang, f"Окно {label} закрыто, но программа продолжает работать в фоне (в трее).",
                                          f"{label}'s window is closed, but the app keeps running in the background (tray)."),
                            "next": {"action": "app.close", "params": {"name": p["name"]},
                                     "label": tr(lang, "Завершить полностью", "Quit completely")},
                            "data": {"app": label, "tray": True}}
                return {"message": tr(lang, f"Закрываю {label} — программа ещё открыта (возможно, спрашивает о сохранении).",
                                      f"Closing {label} — it is still open (it may be asking to save)."),
                        "data": {"app": label}}
            return {"message": tr(lang, f"Закрыл {label}", f"Closed {label}") if gone else
                    tr(lang, f"Закрываю {label} (окон: {len(wins)})", f"Closing {label} ({len(wins)} window(s))"),
                    "data": {"app": label}}

        from .assistant_actions_win import PROTECTED_PROCESSES
        pids = [int(r["pid"]) for r in procs if str(r.get("exe", "")).lower() not in PROTECTED_PROCESSES]
        if not pids:
            raise ActionError(tr(lang, f"{label} — системный процесс, его не завершаю.",
                                 f"{label} is a system process — not ending it."), "blocked")
        for pid in pids:
            try:
                await self._blocking(self.backend.close_pid, pid)
            except Exception:
                pass
        gone = await self._blocking(self._wait_exit, pids, 2.5)
        forced = False
        if gone is not True:
            for pid in pids:
                try:
                    if gone is None or self.backend.pid_alive(pid):
                        await self._blocking(self.backend.kill, pid)
                        forced = True
                except ActionError:
                    raise
                except Exception:
                    pass
            gone = await self._blocking(self._wait_exit, pids, 1.5)
        if gone is False:
            raise ActionError(tr(lang, f"{label} не завершается (возможно, нужны права администратора).",
                                 f"{label} does not quit (administrator rights may be needed)."), "failed")
        return {"message": tr(lang, f"{label} завершена" + (" (процесс остановлен)" if forced else ""),
                              f"{label} has quit" + (" (process ended)" if forced else "")),
                "data": {"app": label, "tray": True, "forced": forced}}

    async def h_app__switch(self, p, *, lang, **_):
        found = await self._blocking(self._app_targets, p["name"])
        wins, procs = found["windows"], found["procs"]
        if not wins and procs and procs[0].get("path"):

            await self._hide_quick()
            await self._blocking(self.backend.open_target, procs[0]["path"])
            return {"message": tr(lang, f"Показываю {found['label']}", f"Showing {found['label']}")}
        if not wins:
            raise ActionError(tr(lang, f"«{p['name']}» не запущено.", f"“{p['name']}” is not running."), "not_running")
        await self._hide_quick()
        await self._blocking(self.backend.focus_window, wins[0]["hwnd"])
        return {"message": wins[0].get("title") or p["name"]}

    async def h_app__list(self, p, *, lang, **_):
        rows = await self._blocking(self.backend.windows)
        items = [{"title": w["title"][:80], "app": w.get("app", ""), "state": w.get("state")} for w in rows if not w.get("own")][:30]
        return {"message": tr(lang, f"Открыто окон: {len(items)}", f"{len(items)} windows open"), "kind": "list",
                "list": [f"{i['app']} — {i['title']}" + (" (свернуто)" if i["state"] == "minimized" and lang != "en" else
                                                         " (minimized)" if i["state"] == "minimized" else "") for i in items],
                "data": {"windows": items}}

    async def _open(self, target: str) -> None:
        if sys.platform == "win32":
            await self._blocking(self.backend.open_target, target)
        elif target.startswith(("http://", "https://")):

            if os.environ.get("MEMUAI_ASSISTANT_OPEN_BROWSER") == "1":
                webbrowser.open(target)
        else:
            raise Unsupported()

    async def h_open__url(self, p, *, lang, **_):
        url = normalize_url(p["url"])
        await self._open(url)
        return {"message": tr(lang, "Открываю ", "Opening ") + url, "links": [{"label": url, "url": url}]}

    async def h_open__path(self, p, *, lang, **_):
        path = os.path.expandvars(os.path.expanduser(p["path"]))
        if not os.path.exists(path):
            raise ActionError(tr(lang, f"Нет такого файла или папки: {path}", f"No such file or folder: {path}"), "not_found")
        await self._open(path)
        return {"message": tr(lang, "Открываю ", "Opening ") + path}

    async def h_open__folder(self, p, *, lang, **_):
        path = known_folder(p["folder"])
        if p["folder"] == "screenshots":
            os.makedirs(path, exist_ok=True)
        await self._open(path)
        return {"message": tr(lang, "Открываю папку ", "Opening ") + path}

    async def h_open__settings(self, p, *, lang, **_):
        uri = catalog.SETTINGS_PAGES.get(p.get("page", ""), "ms-settings:")
        await self._open(uri)
        page = str(p.get("page") or "")
        label = _SETTINGS_LABELS.get(page)
        label = (label[1] if lang == "en" else label[0]) if label else page
        return {"message": tr(lang, "Открываю параметры Windows", "Opening Windows Settings") + (f" · {label}" if label else "")}

    async def h_web__search(self, p, *, lang, **_):
        url = search_url(p["query"], p.get("engine") or self.settings()["search_engine"])
        await self._open(url)
        return {"message": tr(lang, f"Ищу «{p['query']}»", f"Searching for “{p['query']}”"), "links": [{"label": url, "url": url}]}

    async def h_media__search(self, p, *, lang, **_):
        service = p.get("service") or self.settings()["music_service"]
        url = media_url(p["query"], service)
        await self._open(url)
        names = {"youtube": "YouTube", "youtube_music": "YouTube Music", "yandex_music": tr(lang, "Яндекс Музыка", "Yandex Music"),
                 "spotify": "Spotify", "vk": tr(lang, "VK Музыка", "VK Music"), "soundcloud": "SoundCloud"}
        return {"message": tr(lang, f"Ищу «{p['query']}» в {names.get(service, service)}",
                              f"Searching “{p['query']}” on {names.get(service, service)}"), "links": [{"label": url, "url": url}]}

    async def h_food__order(self, p, *, lang, **_):
        service = p.get("service") or default_service("food", self.settings(), lang)
        url = food_url(p["query"], service)
        await self._open(url)
        names = {"yandex_eda": tr(lang, "Яндекс Еде", "Yandex Eda"), "delivery_club": tr(lang, "Яндекс Еде", "Yandex Eda"),
                 "samokat": tr(lang, "Самокате", "Samokat"), "kuper": tr(lang, "Купере", "Kuper"), "ubereats": "Uber Eats",
                 "doordash": "DoorDash", "deliveroo": "Deliveroo", "wolt": "Wolt"}
        return {"message": tr(lang, f"Открыл поиск «{p['query']}» в {names.get(service, service)}. Заказ и оплату вы оформляете сами — "
                                    "MemuAI не заказывает и не платит за вас.",
                              f"Opened a search for “{p['query']}” on {names.get(service, service)}. You place and pay for "
                              "the order yourself — MemuAI never orders or pays on your behalf."),
                "links": [{"label": url, "url": url}]}

    async def h_shop__search(self, p, *, lang, **_):
        service = p.get("service") or default_service("shop", self.settings(), lang)
        url = shop_url(p["query"], service)
        await self._open(url)
        return {"message": tr(lang, f"Открыл поиск «{p['query']}». Покупку оформляете вы — MemuAI ничего не покупает.",
                              f"Opened a search for “{p['query']}”. You make the purchase — MemuAI never buys anything."),
                "links": [{"label": url, "url": url}]}

    async def h_taxi__order(self, p, *, lang, **_):
        service = p.get("service") or default_service("taxi", self.settings(), lang)
        url = taxi_url(p.get("to", ""), service)
        await self._open(url)
        return {"message": tr(lang, "Открыл сервис такси" + (f" (куда: {p['to']})" if p.get("to") else "") +
                              ". Подтверждаете поездку вы — MemuAI не вызывает машину сам.",
                              "Opened the taxi service" + (f" (to: {p['to']})" if p.get("to") else "") +
                              ". You confirm the ride — MemuAI never books it by itself."), "links": [{"label": url, "url": url}]}

    async def h_maps__route(self, p, *, lang, **_):
        service = p.get("service") or default_service("map", self.settings(), lang)
        url = map_url(p["to"], p.get("from", ""), service)
        await self._open(url)
        return {"message": tr(lang, f"Открыл карту: {p['to']}", f"Opened the map: {p['to']}"), "links": [{"label": url, "url": url}]}

    async def h_weather__get(self, p, *, lang, **_):
        from . import instant_answers
        city = p.get("city") or ""
        if not city:
            try:
                from . import panels
                from .storage import load_json_with_recovery
                doc = panels._normalize_document(load_json_with_recovery(panels._path(), {}))
                loc = (doc.get("settings") or {}).get("location") or {}
                city = str(loc.get("name") or "") if loc.get("mode") in ("manual", "auto") else ""
            except Exception:
                city = ""
        when = p.get("when") or "now"
        query = (("погода " + (f"в {city} " if city else "") + {"tomorrow": "завтра", "week": "на неделю"}.get(when, "сейчас"))
                 if lang != "en" else ("weather " + (f"in {city} " if city else "") + {"tomorrow": "tomorrow", "week": "this week"}.get(when, "now")))
        answer = await asyncio.wait_for(instant_answers.weather(query, lang), 15)
        if not answer:
            raise ActionError(tr(lang, "Не удалось получить погоду (нет сети или город не найден).",
                                 "Couldn't get the weather (offline or the city was not found)."), "source")
        lines = [line for line in str(answer.get("text") or "").splitlines() if line.strip()]
        return {"message": lines[1] if len(lines) > 1 else answer.get("title", ""), "kind": "weather",
                "details": [[line.split(":", 1)[0], line.split(":", 1)[1].strip()] for line in lines[2:5] if ":" in line],
                "data": {"title": answer.get("title"), "text": answer.get("text"), "approximate": answer.get("approximate")},
                "links": [{"label": answer.get("source") or "Open-Meteo", "url": answer.get("url") or "https://open-meteo.com/"}],
                "speak": (answer.get("title") or "") + ". " + (lines[1] if len(lines) > 1 else "")}

    async def h_currency__convert(self, p, *, lang, **_):
        from . import instant_answers
        src, dst = str(p.get("from") or "").strip(), str(p.get("to") or "").strip() or ("RUB" if lang != "en" else "USD")

        src = src.upper() if src.upper() in ("BTC", "ETH") else instant_answers.currency_code(src)
        dst = instant_answers.currency_code(dst)
        if not src or not dst:
            raise ActionError(tr(lang, f"Не знаю валюту «{p.get('from') if not src else p.get('to')}». Примеры: USD, EUR, рубли, юани.",
                                 f"Unknown currency “{p.get('from') if not src else p.get('to')}”. Examples: USD, EUR, yen."), "bad_params")
        amount = p.get("amount") or 1
        if src in ("BTC", "ETH"):
            answer = await asyncio.wait_for(instant_answers.crypto(f"курс {src}", lang), 15)
        else:
            answer = await asyncio.wait_for(instant_answers.currency(f"{amount} {src} {'to' if lang == 'en' else 'в'} {dst}", lang), 15)
        if not answer:
            raise ActionError(tr(lang, "Курс сейчас недоступен (нет сети или неизвестная валюта).",
                                 "The rate is unavailable (offline or unknown currency)."), "source")
        return {"message": answer.get("text", ""), "kind": "currency", "details": [[tr(lang, "Примечание", "Note"), answer["note"]]]
                if answer.get("note") else None, "links": [{"label": answer.get("source") or "", "url": answer.get("url") or ""}]}

    async def h_time__now(self, p, *, lang, **_):
        now = self.now_fn()
        days_ru = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        months_ru = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
        text = (now.strftime("%H:%M") + f", {days_ru[now.weekday()]}, {now.day} {months_ru[now.month - 1]} {now.year}"
                if lang != "en" else now.strftime("%H:%M, %A, %B %d, %Y"))
        return {"message": text, "kind": "time", "speak": text}

    async def h_volume__get(self, p, *, lang, **_):
        state = await self._blocking(self.backend.volume_get)
        return {"message": tr(lang, f"Громкость {state['level']}%", f"Volume {state['level']}%") +
                (tr(lang, " (звук выключен)", " (muted)") if state.get("mute") else ""), "kind": "level",
                "data": {"level": state["level"], "mute": state.get("mute"), "icon": "volume"}}

    async def h_volume__set(self, p, *, lang, **_):
        before = await self._blocking(self.backend.volume_get)
        if "level" in p:
            level = int(p["level"])
        elif "delta" in p:
            level = int(before["level"]) + int(p["delta"])
        else:
            raise ActionError(tr(lang, "Укажите уровень громкости", "Say the volume level"), "bad_params")
        level = max(0, min(100, level))
        after = await self._blocking(self.backend.volume_set, level)
        return {"message": tr(lang, f"Громкость {after['level']}%", f"Volume {after['level']}%"), "kind": "level",
                "data": {"level": after["level"], "before": before["level"], "icon": "volume"},

                "undo": {"action": "_volume.restore", "params": {"level": before["level"], "mute": bool(before.get("mute"))}}}

    async def h__volume__restore(self, p, *, lang, **_):
        after = await self._blocking(self.backend.volume_set, max(0, min(100, int(p.get("level") or 0))))
        if bool(p.get("mute")) != bool(after.get("mute")):
            after = await self._blocking(self.backend.volume_mute, bool(p.get("mute")))
        return {"message": tr(lang, f"Громкость {after['level']}%", f"Volume {after['level']}%") +
                (tr(lang, ", звук выключен", ", muted") if after.get("mute") else ""), "kind": "level",
                "data": {"level": after["level"], "mute": after.get("mute"), "icon": "volume"}}

    async def h_volume__mute(self, p, *, lang, **_):
        before = await self._blocking(self.backend.volume_get)
        after = await self._blocking(self.backend.volume_mute, p.get("mute"))
        return {"message": tr(lang, "Звук выключен", "Muted") if after.get("mute") else tr(lang, "Звук включён", "Unmuted"),
                "kind": "level", "data": {"level": after["level"], "mute": after.get("mute"), "icon": "volume"},
                "undo": {"action": "volume.mute", "params": {"mute": bool(before.get("mute"))}}}

    async def h_media__key(self, p, *, lang, **_):
        await self._blocking(self.backend.media_key, p["key"])
        label = {"play_pause": tr(lang, "Пауза / воспроизведение", "Play / pause"), "next": tr(lang, "Следующий трек", "Next track"),
                 "prev": tr(lang, "Предыдущий трек", "Previous track"), "stop": tr(lang, "Стоп", "Stop")}[p["key"]]
        return {"message": label}

    async def h_brightness__get(self, p, *, lang, **_):
        state = await self._blocking(self.backend.brightness_get)
        return {"message": tr(lang, f"Яркость {state['level']}%", f"Brightness {state['level']}%"), "kind": "level",
                "data": {"level": state["level"], "icon": "sun", "displays": state.get("displays")}}

    async def h_brightness__set(self, p, *, lang, **_):
        try:
            before = await self._blocking(self.backend.brightness_get)
        except ActionError:
            before = None
        if "level" in p:
            level = int(p["level"])
        elif "delta" in p:
            if not before:
                raise ActionError(tr(lang, "Не удалось прочитать текущую яркость", "Couldn't read the current brightness"), "no_brightness")
            level = int(before["level"]) + int(p["delta"])
        else:
            raise ActionError(tr(lang, "Укажите уровень яркости", "Say the brightness level"), "bad_params")
        level = max(0, min(100, level))
        await self._blocking(self.backend.brightness_set, level)
        out = {"message": tr(lang, f"Яркость {level}%", f"Brightness {level}%"), "kind": "level",
               "data": {"level": level, "icon": "sun"}}
        if before:
            out["undo"] = {"action": "brightness.set", "params": {"level": before["level"]}}
        return out

    async def h_theme__set(self, p, *, lang, **_):
        before = await self._blocking(self.backend.theme_get)
        dark = (not before["dark"]) if p["mode"] == "toggle" else p["mode"] == "dark"
        await self._blocking(self.backend.theme_set, dark)
        return {"message": tr(lang, "Тёмная тема Windows", "Windows dark mode") if dark else tr(lang, "Светлая тема Windows", "Windows light mode"),
                "undo": {"action": "theme.set", "params": {"mode": "dark" if before["dark"] else "light"}}}

    async def h_nightlight__open(self, p, *, lang, **_):
        await self._open("ms-settings:nightlight")
        return {"message": tr(lang, "У «Ночного света» нет публичного API Windows — открыл его настройки, там один переключатель.",
                              "Night light has no public Windows API — opened its settings page (one switch there).")}

    async def _radio(self, kind: str, p, lang):
        result = await self._blocking(self.backend.radio, kind, bool(p["on"]))
        before_on = str(result.get("before") or "").lower() == "on"
        name = "Wi‑Fi" if kind == "wifi" else "Bluetooth"
        out = {"message": f"{name}: " + (tr(lang, "включён", "on") if p["on"] else tr(lang, "выключен", "off"))}
        if result.get("before"):
            out["undo"] = {"action": f"{kind}.set", "params": {"on": before_on}}
        return out

    async def h_wifi__set(self, p, *, lang, **_):
        return await self._radio("wifi", p, lang)

    async def h_bluetooth__set(self, p, *, lang, **_):
        return await self._radio("bluetooth", p, lang)

    async def h_screenshot__take(self, p, *, lang, **_):
        mode = p.get("mode") or "screen"
        rect = None
        if mode != "region":
            await self._hide_quick()
        if mode == "window":
            win = await self._blocking(self.backend.foreground)
            if not win:
                raise ActionError(tr(lang, "Нет активного окна", "No active window"), "no_window")
            rect = win["rect"]
        result = await self._blocking(self.backend.screenshot, mode, rect, bool(p.get("copy")))
        if result.get("region"):
            return {"message": tr(lang, "Выделите область — снимок попадёт в буфер обмена.",
                                  "Select an area — the snip goes to the clipboard.")}
        return {"message": tr(lang, "Скриншот сохранён", "Screenshot saved"), "details": [[tr(lang, "Файл", "File"), result["path"]]],
                "data": {"path": result["path"]}, "open_path": result["path"]}

    async def h_clipboard__get(self, p, *, lang, **_):
        text = await self._blocking(self.backend.clipboard_get)
        if not text:
            return {"message": tr(lang, "В буфере обмена нет текста.", "No text in the clipboard.")}
        return {"message": text[:2000], "kind": "text", "data": {"length": len(text)}}

    async def h_clipboard__set(self, p, *, lang, **_):

        text = "" if p.get("clear") else str(p.get("text") or "")
        if not text and not p.get("clear"):
            raise ActionError(tr(lang, "Нечего копировать — нужен текст", "Nothing to copy — text is needed"), "bad_params")
        p = {**p, "text": text}
        try:
            before = await self._blocking(self.backend.clipboard_get)
        except ActionError:
            before = None
        await self._blocking(self.backend.clipboard_set, p["text"])
        out = {"message": tr(lang, "Буфер обмена очищен", "Clipboard cleared") if not p["text"].strip()
               else tr(lang, "Скопировано в буфер обмена", "Copied to the clipboard")}
        if before:
            out["undo"] = {"action": "clipboard.set", "params": {"text": before}}
        return out

    async def h_type__text(self, p, *, lang, **_):
        await self._hide_quick()
        await self._blocking(self.backend.type_text, p["text"])
        return {"message": tr(lang, f"Напечатано символов: {len(p['text'])}", f"Typed {len(p['text'])} characters")}

    async def h_keys__press(self, p, *, lang, **_):
        await self._hide_quick()
        await self._blocking(self.backend.send_keys, p["keys"])
        return {"message": p["keys"]}

    async def h_recycle__empty(self, p, *, lang, **_):
        result = await self._blocking(self.backend.recycle_empty)
        if not result.get("emptied"):
            return {"message": tr(lang, "Корзина уже пуста.", "The Recycle Bin is already empty.")}
        return {"message": tr(lang, f"Корзина очищена — объектов: {result['items']}, {human_bytes(result['bytes'], lang)}",
                              f"Recycle Bin emptied: {result['items']} items, {human_bytes(result['bytes'], lang)}")}

    async def h_sysinfo__get(self, p, *, lang, **_):
        what = p.get("what") or "all"
        info = await self._blocking(self.backend.sysinfo, what)
        details: List[List[str]] = []
        cpu, ram, bat = info.get("cpu"), info.get("ram"), info.get("battery")
        if cpu:
            details.append([tr(lang, "Процессор", "CPU"), f"{cpu['percent']}%" + (f" · {cpu['name']}" if cpu.get("name") else "")])
        if ram:
            used = ram["total"] - ram["free"]
            details.append([tr(lang, "Память", "Memory"), f"{ram['percent']}% · {human_bytes(used, lang)} / {human_bytes(ram['total'], lang)}"])
        if bat:
            if bat.get("present"):
                text = f"{bat['percent']}%" + (tr(lang, ", заряжается", ", charging") if bat.get("plugged") else "")
                if bat.get("seconds_left") and not bat.get("plugged"):
                    text += tr(lang, f", осталось ~{intents.duration_text(bat['seconds_left'] // 60 * 60, lang)}",
                               f", ~{intents.duration_text(bat['seconds_left'] // 60 * 60, lang)} left")
            else:
                text = tr(lang, "нет батареи (питание от сети)", "no battery (AC power)")
            details.append([tr(lang, "Батарея", "Battery"), text])
        for disk in info.get("disk") or []:
            details.append([tr(lang, "Диск ", "Disk ") + disk["drive"], tr(lang, f"свободно {human_bytes(disk['free'], lang)} из {human_bytes(disk['total'], lang)}",
                                                                          f"{human_bytes(disk['free'], lang)} free of {human_bytes(disk['total'], lang)}")])
        net = info.get("network")
        if net:
            details.append([tr(lang, "Сеть", "Network"), " · ".join(x for x in (net.get("ip"), net.get("wifi") and "Wi‑Fi " + net["wifi"], net.get("host")) if x)])
        if info.get("uptime") is not None:
            details.append([tr(lang, "Работает", "Uptime"), intents.duration_text(int(info["uptime"]) // 60 * 60, lang)])
        if info.get("os"):
            details.append(["Windows", info["os"]])
        message = details[0][1] if len(details) == 1 else tr(lang, "Сведения о системе", "System info")
        return {"message": message, "details": details, "kind": "sysinfo", "data": info,
                "speak": "; ".join(f"{a}: {b}" for a, b in details[:3])}

    async def h_process__list(self, p, *, lang, **_):
        rows = await self._blocking(self.backend.processes)
        query = (p.get("query") or "").lower()
        if query:
            rows = [r for r in rows if query in r["name"].lower()]
        merged: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            item = merged.setdefault(row["name"].lower(), {"name": row["name"], "count": 0, "memory": 0, "pids": []})
            item["count"] += 1
            item["memory"] += row["memory"]
            item["pids"].append(row["pid"])
        top = sorted(merged.values(), key=lambda r: r["memory"], reverse=True)[:15]
        return {"message": tr(lang, f"Процессов: {len(rows)}", f"{len(rows)} processes"), "kind": "list",
                "list": [f"{r['name']} ×{r['count']} — {human_bytes(r['memory'], lang)}" for r in top], "data": {"top": top}}

    async def h_process__kill(self, p, *, lang, **_):
        from .assistant_actions_win import PROTECTED_PROCESSES
        rows = await self._blocking(self.backend.processes)
        if p.get("pid"):
            victims = [r for r in rows if r["pid"] == p["pid"]]
        elif p.get("name"):
            from . import assistant_apps
            name = p["name"]
            exact = [r for r in rows if r["name"].lower() in (name.lower(), name.lower() + ".exe")]
            victims = exact or [r for r in rows if assistant_apps.match_process(name, r["name"]) >= 0.85]
        else:
            raise ActionError(tr(lang, "Укажите имя процесса или PID", "Give a process name or PID"), "bad_params")
        if not victims:
            raise ActionError(tr(lang, "Такой процесс не запущен", "No such process is running"), "not_running")
        blocked = [r for r in victims if r["name"].lower() in PROTECTED_PROCESSES or r["pid"] in (0, 4, os.getpid())]
        if blocked:
            raise ActionError(tr(lang, f"{blocked[0]['name']} — системный процесс или сам MemuAI, его не завершаю.",
                                 f"{blocked[0]['name']} is a system process or MemuAI itself — not ending it."), "blocked")
        for row in victims[:20]:
            await self._blocking(self.backend.kill, row["pid"])
        return {"message": tr(lang, f"Завершено: {victims[0]['name']} ({len(victims)})", f"Ended: {victims[0]['name']} ({len(victims)})")}

    async def h_lock__screen(self, p, *, lang, **_):
        await self._hide_quick()
        await self._blocking(self.backend.lock)
        return {"message": tr(lang, "Компьютер заблокирован", "Locked")}

    async def h_power__sleep(self, p, *, lang, **_):
        await self._hide_quick()
        await self._blocking(self.backend.sleep)
        return {"message": tr(lang, "Спящий режим", "Going to sleep")}

    async def h_power__hibernate(self, p, *, lang, **_):
        await self._hide_quick()
        await self._blocking(self.backend.hibernate)
        return {"message": tr(lang, "Гибернация", "Hibernating")}

    def _power_delay(self, p, lang: str, restart: bool) -> Tuple[int, str]:
        minutes = int(p.get("delay_minutes") or 0)
        if minutes <= 0:
            return 5, (tr(lang, "Перезагрузка через 5 секунд. Ещё можно отменить.", "Restarting in 5 seconds. You can still cancel.")
                       if restart else tr(lang, "Выключение через 5 секунд. Ещё можно отменить.",
                                          "Shutting down in 5 seconds. You can still cancel."))
        at = (self.now_fn() + timedelta(minutes=minutes)).strftime("%H:%M")
        span = intents.duration_text(minutes * 60, lang)
        what = tr(lang, "Перезагрузка" if restart else "Выключение", "Restart" if restart else "Shutdown")
        return minutes * 60, tr(lang, f"{what} в {at} (через {span}). Отменить — «отмени выключение».",
                                f"{what} at {at} (in {span}). To cancel, say “cancel shutdown”.")

    async def h_power__restart(self, p, *, lang, **_):
        seconds, message = self._power_delay(p, lang, True)
        await self._blocking(self.backend.restart, seconds)
        return {"message": message, "undo": {"action": "power.cancel", "params": {}}}

    async def h_power__shutdown(self, p, *, lang, **_):
        seconds, message = self._power_delay(p, lang, False)
        await self._blocking(self.backend.shutdown, seconds)
        return {"message": message, "undo": {"action": "power.cancel", "params": {}}}

    async def h_power__signout(self, p, *, lang, **_):
        await self._blocking(self.backend.signout)
        return {"message": tr(lang, "Выход из системы", "Signing out")}

    async def h_power__cancel(self, p, *, lang, **_):
        ok = await self._blocking(self.backend.cancel_shutdown)
        return {"message": tr(lang, "Выключение отменено", "Shutdown cancelled") if ok else
                tr(lang, "Отменять нечего — выключение не запланировано.", "Nothing to cancel — no shutdown is pending.")}

    async def h_shell__run(self, p, *, lang, **_):
        result = await self._blocking(self.backend.powershell, p["command"])
        return {"message": tr(lang, f"Код выхода {result['code']}", f"Exit code {result['code']}"), "kind": "code",
                "data": result, "status": "done" if result["code"] == 0 else "error"}

    async def _target_window(self, target: str, lang: str) -> Dict[str, Any]:
        if target and target.lower() not in ("foreground", "active", "current", "this", "активное", "текущее"):
            wins = await self._blocking(self._windows_of, target)
            if not wins:
                raise ActionError(tr(lang, f"Окно «{target}» не найдено", f"No “{target}” window"), "no_window")
            return wins[0]
        await self._hide_quick()
        win = await self._blocking(self.backend.foreground)
        if not win:
            raise ActionError(tr(lang, "Нет активного окна", "No active window"), "no_window")
        return win

    async def h_window__action(self, p, *, lang, **_):
        win = await self._target_window(p.get("target", ""), lang)
        action = p["action"]
        undo = {"action": "_window.place", "params": {"hwnd": win["hwnd"], "rect": win["rect"], "state": win["state"]}}
        if action.startswith("snap_") or action == "center":
            mons = await self._blocking(self.backend.monitors)
            mon = mons[geo.monitor_for_rect(tuple(win["rect"]), mons)]
            rect = geo.center_rect(tuple(win["rect"]), tuple(mon["work"])) if action == "center" else geo.snap_rect(tuple(mon["work"]), action)
            await self._blocking(self.backend.place_window, win["hwnd"], rect, "normal")
        elif action == "close":
            await self._blocking(self.backend.close_window, win["hwnd"])
            undo = None
        else:
            await self._blocking(self.backend.window_cmd, win["hwnd"], action)
            if action in ("topmost", "untopmost"):
                undo = {"action": "_window.cmd", "params": {"hwnd": win["hwnd"], "cmd": "untopmost" if action == "topmost" else "topmost"}}
        names = {"maximize": tr(lang, "Развернул", "Maximized"), "minimize": tr(lang, "Свернул", "Minimized"),
                 "restore": tr(lang, "Восстановил", "Restored"), "snap_left": tr(lang, "Слева", "Snapped left"),
                 "snap_right": tr(lang, "Справа", "Snapped right"), "snap_top": tr(lang, "Сверху", "Top half"),
                 "snap_bottom": tr(lang, "Снизу", "Bottom half"), "center": tr(lang, "По центру", "Centered"),
                 "close": tr(lang, "Закрываю", "Closing"), "topmost": tr(lang, "Поверх всех окон", "Always on top"),
                 "untopmost": tr(lang, "Обычный порядок окон", "Normal z-order")}
        return {"message": f"{names.get(action, action)}: {win['title'][:60]}", "undo": undo}

    async def h__window__place(self, p, *, lang, **_):
        await self._blocking(self.backend.place_window, int(p["hwnd"]), p["rect"], p.get("state", "normal"))
        return {"message": tr(lang, "Окно на прежнем месте", "Window restored")}

    async def h__window__cmd(self, p, *, lang, **_):
        await self._blocking(self.backend.window_cmd, int(p["hwnd"]), p["cmd"])
        return {"message": "OK"}

    async def h_window__move(self, p, *, lang, **_):
        win = await self._target_window(p.get("target", ""), lang)
        mons = await self._blocking(self.backend.monitors)
        if len(mons) < 2:
            raise ActionError(tr(lang, "Подключён только один монитор", "Only one monitor is connected"), "one_monitor")
        cur = geo.monitor_for_rect(tuple(win["rect"]), mons)
        dst = geo.pick_monitor(p.get("monitor", "next"), cur, len(mons))
        rect = geo.move_to_monitor(tuple(win["rect"]), tuple(mons[cur]["work"]), tuple(mons[dst]["work"]))
        await self._blocking(self.backend.place_window, win["hwnd"], rect, win["state"] if win["state"] == "maximized" else "normal")
        return {"message": tr(lang, f"Окно на мониторе {dst + 1}", f"Window moved to monitor {dst + 1}"),
                "undo": {"action": "_window.place", "params": {"hwnd": win["hwnd"], "rect": win["rect"], "state": win["state"]}}}

    async def h_window__resize(self, p, *, lang, **_):
        win = await self._target_window(p.get("target", ""), lang)
        mons = await self._blocking(self.backend.monitors)
        mon = mons[geo.monitor_for_rect(tuple(win["rect"]), mons)]
        work = tuple(mon["work"])
        w = int(geo.width(work) * float(p.get("width") or 60) / 100)
        h = int(geo.height(work) * float(p.get("height") or 70) / 100)
        rect = geo.center_rect((0, 0, w, h), work)
        await self._blocking(self.backend.place_window, win["hwnd"], rect, "normal")
        return {"message": tr(lang, f"Размер окна {w}×{h}", f"Window size {w}×{h}"),
                "undo": {"action": "_window.place", "params": {"hwnd": win["hwnd"], "rect": win["rect"], "state": win["state"]}}}

    async def h_window__arrange(self, p, *, lang, **_):
        await self._hide_quick()
        wins = [w for w in await self._blocking(self.backend.windows, False) if not w.get("own")]
        mons = await self._blocking(self.backend.monitors)
        fg = await self._blocking(self.backend.foreground)
        mon_index = geo.monitor_for_rect(tuple(fg["rect"]), mons) if fg else 0
        mine = [w for w in wins if geo.monitor_for_rect(tuple(w["rect"]), mons) == mon_index][:9]
        if not mine:
            raise ActionError(tr(lang, "На этом мониторе нет окон", "No windows on this monitor"), "no_window")
        rects = geo.tile(len(mine), tuple(mons[mon_index]["work"]), p.get("layout") or "side_by_side")
        before = [{"hwnd": w["hwnd"], "rect": w["rect"], "state": w["state"]} for w in mine]
        for win, rect in zip(mine, rects):
            await self._blocking(self.backend.place_window, win["hwnd"], rect, "normal")
        return {"message": tr(lang, f"Расставил окна: {len(mine)}", f"Arranged {len(mine)} windows"),
                "undo": {"action": "_windows.restore", "params": {"windows": before}}}

    async def h__windows__restore(self, p, *, lang, **_):
        for row in p.get("windows") or []:
            try:
                await self._blocking(self.backend.place_window, int(row["hwnd"]), row["rect"], row.get("state", "normal"))
            except ActionError:
                continue
        return {"message": tr(lang, "Окна на прежних местах", "Windows restored")}

    async def h_window__minimize_all(self, p, *, lang, **_):
        await self._hide_quick()
        await self._blocking(self.backend.send_keys, "Win+M")
        return {"message": tr(lang, "Все окна свёрнуты", "All windows minimized"), "undo": {"action": "_keys", "params": {"keys": "Win+Shift+M"}}}

    async def h__keys(self, p, *, lang, **_):
        await self._blocking(self.backend.send_keys, p["keys"])
        return {"message": "OK"}

    async def h_desktop__virtual(self, p, *, lang, **_):
        keys = {"new": "Win+Ctrl+D", "next": "Win+Ctrl+Right", "prev": "Win+Ctrl+Left", "close": "Win+Ctrl+F4", "view": "Win+Tab"}[p["action"]]
        await self._hide_quick()
        await self._blocking(self.backend.send_keys, keys)
        names = {"new": tr(lang, "Новый рабочий стол", "New desktop"), "next": tr(lang, "Следующий рабочий стол", "Next desktop"),
                 "prev": tr(lang, "Предыдущий рабочий стол", "Previous desktop"), "close": tr(lang, "Рабочий стол закрыт", "Desktop closed"),
                 "view": tr(lang, "Представление задач", "Task view")}
        return {"message": names[p["action"]]}

    async def h_layout__capture(self, p, *, lang, **_):
        layout = await self.capture_layout(p.get("name") or "")
        name = layout["name"]

        def save(data):
            data["layouts"][name] = layout
            while len(data["layouts"]) > MAX_LAYOUTS:
                data["layouts"].pop(next(iter(data["layouts"])))
        await self._blocking(self.store.update, save)
        return {"message": tr(lang, f"Раскладка «{name}» сохранена: окон {len(layout['windows'])}",
                              f"Layout “{name}” saved: {len(layout['windows'])} windows"), "kind": "layout",
                "list": [f"{w['app']['name'] or w['app']['exe']} — {tr(lang, 'монитор', 'monitor')} {w['monitor'] + 1}" for w in layout["windows"]],
                "data": {"layout": layout}}

    async def capture_layout(self, name: str = "") -> Dict[str, Any]:
        await self._hide_quick()
        wins = [w for w in await self._blocking(self.backend.windows) if not (w.get("own") and "Quick" in w.get("title", ""))]
        mons = await self._blocking(self.backend.monitors)
        stamp = self.now_fn().strftime("%d.%m %H:%M")
        layout = geo.capture(wins, mons, name=name or stamp)
        if not layout["windows"]:
            raise ActionError("Нет открытых окон для сохранения", "no_window", "No open windows to save")
        return layout

    async def h_layout__restore(self, p, *, lang, **_):
        data = await self._blocking(self.store.load)
        name = p["name"]
        layout = data["layouts"].get(name)
        if layout is None:
            low = name.lower()
            layout = next((v for k, v in data["layouts"].items() if k.lower() == low), None)
            if layout is None:
                close = sorted(data["layouts"], key=lambda k: -intents.similarity(k, name))
                if close and intents.similarity(close[0], name) > 0.7:
                    layout = data["layouts"][close[0]]
        if layout is None:
            raise ActionError(tr(lang, f"Раскладка «{name}» не найдена. Сохранённые: ", f"Layout “{name}” not found. Saved: ") +
                              (", ".join(list(data["layouts"])[:8]) or "—"), "not_found")
        report = await self.restore_layout(layout, launch=True, timeout=25, lang=lang)
        return {"message": tr(lang, f"Раскладка восстановлена: окон {report['placed']} из {report['total']}",
                              f"Layout restored: {report['placed']} of {report['total']} windows"), "kind": "layout",
                "list": report["notes"]}

    async def restore_layout(self, layout: Dict[str, Any], *, launch: bool = True, timeout: float = 25,
                             lang: str = "ru") -> Dict[str, Any]:
        """Launch missing apps, wait for their windows, place everything (bottom-most first)."""
        from . import assistant_apps
        await self._hide_quick()
        mons = await self._blocking(self.backend.monitors)
        running = await self._blocking(self.backend.windows)
        steps = geo.plan_restore(layout, mons, running)
        notes: List[str] = []
        launched = []
        if launch:
            for step in steps:
                if not step["launch"]:
                    continue
                app = step["row"]["app"]
                entry = None
                if app.get("aumid"):
                    entry = {"name": app["name"], "target": "shell:AppsFolder\\" + app["aumid"], "kind": "uwp"}
                elif app.get("path") and os.path.exists(app["path"]):
                    entry = {"name": app["name"], "target": app["path"], "kind": "path"}
                else:
                    found = await self._blocking(self._resolve_app, app.get("name") or app.get("exe") or "")
                    entry = found["match"]
                if entry is None:
                    notes.append(f"✗ {app.get('name') or app.get('exe')}: " + tr(lang, "не найдено", "not found"))
                    continue
                try:
                    await self._blocking(self.backend.launch_app, entry)
                    launched.append(step)
                except ActionError as exc:
                    notes.append(f"✗ {app.get('name')}: {exc.text(lang)}")
        if launched:
            def key(row):
                return (row.get("z"), row["app"].get("exe"), row["app"].get("name"), row["app"].get("path"))
            waiting = {key(step["row"]) for step in launched}
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                await asyncio.sleep(0.8)
                running = await self._blocking(self.backend.windows)
                steps = geo.plan_restore(layout, mons, running)
                if not any(step["window"] is None and key(step["row"]) in waiting for step in steps):
                    await asyncio.sleep(0.6)
                    running = await self._blocking(self.backend.windows)
                    steps = geo.plan_restore(layout, mons, running)
                    break
        placed = 0
        for step in steps:
            if step["window"] is None:
                continue
            win = running[step["window"]]
            try:
                await self._blocking(self.backend.place_window, win["hwnd"], step["rect"], step["state"])
                placed += 1
                notes.append(f"✓ {step['row']['app'].get('name') or win.get('app')} → {step['monitor'] + 1}")
            except ActionError as exc:
                notes.append(f"✗ {win.get('app')}: {exc.text(lang)}")
            await asyncio.sleep(0.05)
        return {"placed": placed, "total": len(steps), "notes": notes[:30]}

    async def h_timer__start(self, p, *, lang, **_):
        timer = self.timers.add_timer(p["seconds"], p.get("label", ""))
        return {"message": tr(lang, "Таймер на ", "Timer for ") + intents.duration_text(p["seconds"], lang) +
                (f" · {timer['label']}" if timer.get("label") else ""), "kind": "timer", "data": {"timer": timer},
                "undo": {"action": "_timer.drop", "params": {"id": timer["id"]}}}

    async def h__timer__drop(self, p, *, lang, **_):
        self.timers.cancel(p["id"], kind=None)
        return {"message": tr(lang, "Таймер удалён", "Timer removed")}

    async def h__timer__restore(self, p, *, lang, **_):
        for timer in p.get("timers") or []:
            self.timers.restore(timer)
        return {"message": tr(lang, "Таймеры возвращены", "Timers restored")}

    async def h_timer__cancel(self, p, *, lang, **_):
        removed = self.timers.cancel(p.get("label", ""), all_=bool(p.get("all")), kind="timer")
        if not removed:
            raise ActionError(tr(lang, "Активных таймеров нет", "No active timers") if not p.get("label")
                              else tr(lang, f"Таймер «{p['label']}» не найден", f"No “{p['label']}” timer"), "not_found")
        return {"message": tr(lang, f"Отменено таймеров: {len(removed)}", f"Cancelled {len(removed)} timer(s)"),
                "undo": {"action": "_timer.restore", "params": {"timers": removed}}}

    async def h_timer__pause(self, p, *, lang, **_):
        changed = self.timers.pause(p.get("label", ""), resume=bool(p.get("resume")))
        if not changed:
            raise ActionError(tr(lang, "Нечего ставить на паузу", "Nothing to pause"), "not_found")
        return {"message": (tr(lang, "Таймер продолжен", "Timer resumed") if p.get("resume") else tr(lang, "Таймер на паузе", "Timer paused")),
                "undo": {"action": "timer.pause", "params": {"label": p.get("label", ""), "resume": not p.get("resume")}}}

    async def h_timer__list(self, p, *, lang, **_):
        snap = self.timers.snapshot()
        lines = []
        for t in snap["timers"]:
            kind = {"timer": tr(lang, "Таймер", "Timer"), "alarm": tr(lang, "Будильник", "Alarm"),
                    "reminder": tr(lang, "Напоминание", "Reminder")}.get(t["kind"], t["kind"])
            label = t.get("label") or t.get("text") or ""
            if t["kind"] == "timer":
                lines.append(f"{kind}{' · ' + label if label else ''}: {intents.duration_text(int(t['remaining']), lang)}"
                             + (tr(lang, " (пауза)", " (paused)") if t.get("paused") else ""))
            else:
                lines.append(f"{kind}{' · ' + label if label else ''}: {datetime.fromtimestamp(t['ends_at']).strftime('%d.%m %H:%M')}")
        if snap["stopwatch"].get("running") or snap["stopwatch"].get("value"):
            lines.append(tr(lang, "Секундомер: ", "Stopwatch: ") + intents.duration_text(int(snap["stopwatch"]["value"]), lang))
        if snap["pomodoro"].get("active"):
            lines.append("Pomodoro: " + phase_name(snap["pomodoro"].get("phase"), lang) + " · " +
                         intents.duration_text(int(snap["pomodoro"].get("remaining") or 0), lang))
        return {"message": tr(lang, "Нет активных таймеров", "No active timers") if not lines else
                tr(lang, f"Активно: {len(lines)}", f"{len(lines)} active"), "kind": "list", "list": lines, "data": snap}

    async def h_stopwatch__control(self, p, *, lang, **_):
        state = self.timers.stopwatch(p["action"])
        text = {"start": tr(lang, "Секундомер запущен", "Stopwatch started"), "stop": tr(lang, "Секундомер остановлен", "Stopwatch stopped"),
                "reset": tr(lang, "Секундомер сброшен", "Stopwatch reset"), "lap": tr(lang, "Круг", "Lap")}[p["action"]]
        if p["action"] in ("stop", "lap"):
            text += ": " + intents.duration_text(int(state["value"]), lang)
        return {"message": text, "kind": "stopwatch", "data": state}

    async def h_alarm__set(self, p, *, lang, **_):
        now = self.now_fn()
        match = _HHMM.match(p["time"])
        if not match:
            raise ActionError(tr(lang, "Время будильника — ЧЧ:ММ", "Alarm time must be HH:MM"), "bad_params")
        when = now.replace(hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0)
        if p.get("date"):
            try:
                day = datetime.strptime(p["date"], "%Y-%m-%d")
                when = when.replace(year=day.year, month=day.month, day=day.day)
            except ValueError:
                raise ActionError(tr(lang, "Дата будильника — ГГГГ-ММ-ДД", "Alarm date must be YYYY-MM-DD"), "bad_params") from None
        if when <= now:
            when += timedelta(days=1)
        timer = self.timers.add_timer(0, p.get("label", ""), kind="alarm", ends_at=when.timestamp())
        return {"message": tr(lang, "Будильник на ", "Alarm at ") + when.strftime("%H:%M") +
                (tr(lang, " завтра", " tomorrow") if when.date() != now.date() else ""), "kind": "timer",
                "data": {"timer": timer}, "undo": {"action": "_timer.drop", "params": {"id": timer["id"]}}}

    async def h_pomodoro__control(self, p, *, lang, **_):
        action = p["action"]
        if action == "stats":
            stats = self.timers.pomodoro_stats()
            return {"message": tr(lang, f"Сегодня: {stats['today']['sessions']} помидоров, {stats['today']['minutes']} мин",
                                  f"Today: {stats['today']['sessions']} pomodoros, {stats['today']['minutes']} min"),
                    "details": [[tr(lang, "За 7 дней", "Last 7 days"), tr(lang, f"{stats['week']['sessions']} · {stats['week']['minutes']} мин",
                                                                        f"{stats['week']['sessions']} · {stats['week']['minutes']} min")]],
                    "kind": "pomodoro", "data": stats}
        options = {**self.settings()["pomodoro"], **{k: p[k] for k in ("work", "short_break", "long_break", "cycles") if k in p}}
        state = self.timers.pomodoro(action, options)
        text = {"start": tr(lang, f"Pomodoro: {options['work']} мин работы", f"Pomodoro: {options['work']} min of focus"),
                "stop": tr(lang, "Pomodoro остановлен", "Pomodoro stopped"), "pause": tr(lang, "Pomodoro на паузе", "Pomodoro paused"),
                "resume": tr(lang, "Pomodoro продолжен", "Pomodoro resumed"),
                "skip": tr(lang, "Следующая фаза: ", "Next phase: ") + phase_name(state.get("phase"), lang)}[action]
        return {"message": text, "kind": "pomodoro", "data": state}

    async def h_reminder__create(self, p, *, lang, **_):
        when = parse_when(p, self.now_fn())
        channel = p.get("channel") or self.settings()["reminder_channel"]
        text = p["text"].strip()
        ids: Dict[str, str] = {}
        notes: List[List[str]] = []
        if channel in ("telegram", "both"):
            status = telegram_status()
            if not status["ok"]:
                raise ActionError(telegram_problem(status, lang), "telegram_off")
            ids["telegram"] = await self._blocking(add_reminder_task, text, when, lang)
            notes.append(["Telegram", tr(lang, "отправит планировщик MemuAI Proxy (Proxy должен быть запущен)",
                                         "sent by the MemuAI Proxy scheduler (Proxy must be running)")])
        if channel in ("local", "both"):
            timer = self.timers.add_timer(0, text[:60], kind="reminder", text=text, ends_at=when.timestamp())
            ids["local"] = timer["id"]
            notes.append([tr(lang, "Этот компьютер", "This computer"), tr(lang, "уведомление и звук", "notification and sound")])
        day = when.strftime("%d.%m ") if when.date() != self.now_fn().date() else ""
        return {"message": tr(lang, f"Напомню {day}в {when.strftime('%H:%M')}: {text}", f"I'll remind you {day}at {when.strftime('%H:%M')}: {text}"),
                "details": notes, "kind": "reminder", "data": {"at": when.isoformat(timespec="minutes"), "ids": ids, "channel": channel},
                "undo": {"action": "_reminder.drop", "params": {"ids": ids}}}

    async def h__reminder__drop(self, p, *, lang, **_):
        ids = p.get("ids") or {}
        if ids.get("local"):
            self.timers.cancel(ids["local"], kind=None)
        if ids.get("telegram"):
            await self._blocking(remove_reminder_task, ids["telegram"])
        return {"message": tr(lang, "Напоминание удалено", "Reminder removed")}

    async def h_reminder__list(self, p, *, lang, **_):
        lines = []
        for t in self.timers.snapshot()["timers"]:
            if t["kind"] == "reminder":
                lines.append(f"{datetime.fromtimestamp(t['ends_at']).strftime('%d.%m %H:%M')} — {t.get('text') or t.get('label')}")
        for task in await self._blocking(list_reminder_tasks):
            lines.append(f"{task['when']} — {task['text']} (Telegram)")
        return {"message": tr(lang, "Напоминаний нет", "No reminders") if not lines else tr(lang, f"Напоминаний: {len(lines)}", f"{len(lines)} reminders"),
                "kind": "list", "list": sorted(lines)}

    async def h_reminder__cancel(self, p, *, lang, **_):
        ref = p.get("id") or p.get("text") or ""
        removed = self.timers.cancel(ref, kind="reminder")
        count = len(removed) + await self._blocking(remove_reminder_task, ref, match_text=True)
        if not count:
            raise ActionError(tr(lang, "Такое напоминание не найдено", "No such reminder"), "not_found")
        return {"message": tr(lang, f"Удалено напоминаний: {count}", f"Removed {count} reminder(s)")}

    async def h_image__generate(self, p, *, lang, **_):
        if self.proxy_sse is None:
            raise ActionError(tr(lang, "Генерация изображений недоступна", "Image generation is unavailable"), "unavailable")
        model = p.get("model") or self.settings()["image_model"] or await self._default_image_model()
        if not model:
            raise ActionError(tr(lang, "Нет модели для изображений: выберите её в Настройки → Быстрый доступ → Команды или подключите в MemuAI Proxy.",
                                 "No image model: pick one in Settings → Quick Ask → Commands or add one in MemuAI Proxy."), "no_model")
        body = json.dumps({"prompt": p["prompt"], "model": model, "aspect": p.get("aspect") or "auto", "images": []},
                          ensure_ascii=False).encode("utf-8")
        urls: List[str] = []
        error = ""
        buffer = ""

        async def consume():
            nonlocal buffer, error, urls
            async for chunk in self.proxy_sse("/api/admin/pg/image", body):
                buffer += chunk if isinstance(chunk, str) else bytes(chunk).decode("utf-8", "replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        continue
                    try:
                        item = json.loads(raw)
                    except ValueError:
                        continue
                    if item.get("status") == "done":
                        urls = [str(u) for u in item.get("urls") or []]
                    elif item.get("status") == "error":
                        error = str(item.get("error") or "error")
        await asyncio.wait_for(consume(), 240)
        if error or not urls:
            raise ActionError(error or tr(lang, "Модель не вернула изображение", "The model returned no image"), "model")
        try:
            from . import media_cache
            urls = await media_cache.localize_urls(urls[:4])
        except Exception:
            pass
        return {"message": tr(lang, "Готово", "Done") + f" · {p['prompt'][:80]}", "kind": "image", "images": urls[:4],
                "data": {"model": model, "prompt": p["prompt"]}}

    async def _default_image_model(self) -> str:
        if self.proxy_json is None:
            return ""
        try:
            status, data = await self.proxy_json("GET", "/api/admin/pg/state")
            models = (data or {}).get("image_models") or [] if status < 400 else []

            try:
                from .pgdata import load_preferences
                chosen = str(load_preferences().get("media_image_model") or "")
            except Exception:
                chosen = ""
            if chosen and chosen != "off" and chosen in models:
                return chosen
            return str(models[0]) if models else ""
        except Exception:
            return ""

    async def h_note__add(self, p, *, lang, **_):
        message_id = await self._blocking(add_note, p["text"], lang)
        return {"message": tr(lang, "Заметка сохранена в чат «Заметки»", "Note saved to the “Notes” chat"),
                "undo": {"action": "_note.drop", "params": {"created": message_id}}}

    async def h__note__drop(self, p, *, lang, **_):
        await self._blocking(remove_note, p["created"])
        return {"message": tr(lang, "Заметка удалена", "Note removed")}

    async def _handoff(self, handoff: Dict[str, Any]) -> bool:
        manager = self.quick_manager()
        if self.app_state is not None:
            self.app_state.quick_handoff = handoff
        if manager is None:
            return False
        try:
            return bool(await self._blocking(manager.open_main, handoff))
        except Exception:
            return False

    async def h_memuai__chat(self, p, *, lang, **_):
        opened = await self._handoff({"draft": p["text"], "title": p["text"][:60]})
        return {"message": tr(lang, "Открываю MemuAI с этим текстом", "Opening MemuAI with this text") if opened
                else tr(lang, "Текст ждёт в MemuAI — откройте главное окно.", "The text is waiting in MemuAI — open the main window.")}

    async def h_memuai__open(self, p, *, lang, **_):
        target = p.get("target") or "main"
        handoff: Dict[str, Any] = {"open": target}
        tabs = {"settings": "general", "quick_settings": "quick", "voice": "voice", "models": "models", "appearance": "appearance",
                "panels": "panels", "modules": "modules", "integrations": "integrations", "prompts": "prompts", "remote": "remote",
                "work": "work", "docs": "docs", "about": "about", "apps": "apps"}
        if target in tabs:
            handoff["settings"] = tabs[target]
        elif target == "new_chat":
            handoff["new_chat"] = True
        opened = await self._handoff(handoff)
        return {"message": tr(lang, "Открываю MemuAI", "Opening MemuAI") + ("" if opened else tr(lang, " (окно откроется при запуске)", " (on next launch)"))}

    async def h_assistant__undo(self, p, *, lang, **_):
        card = await self.undo("", lang)
        if card["status"] != "done":
            raise ActionError(card.get("message") or "", card.get("code") or "empty")
        return {"message": card.get("title", "") + (" — " + card["message"] if card.get("message") else "")}

    async def h_assistant__help(self, p, *, lang, **_):
        return {"message": tr(lang, "Говорите обычными фразами — вот примеры:", "Just say it — for example:"), "kind": "help",
                "data": {"groups": help_groups(lang), "commands": [c["name"] for c in self.store.load()["commands"] if c["enabled"]]}}

    async def h_command__run(self, p, *, lang, source="local", depth=0, **_):
        command = self._find_command(p.get("id") or p.get("name"))
        if not command:
            raise ActionError(tr(lang, f"Своя команда «{p.get('name')}» не найдена", f"Custom command “{p.get('name')}” not found"), "not_found")
        if not command.get("enabled", True):
            raise ActionError(tr(lang, "Эта команда выключена", "This command is disabled"), "disabled")
        if depth >= 3:
            raise ActionError(tr(lang, "Слишком глубокая вложенность команд", "Commands nested too deeply"), "depth")
        report = await self.run_routine(command, lang=lang, depth=depth + 1)
        failed = [s for s in report if s["status"] == "error"]
        done = len([s for s in report if s["status"] == "done"])
        return {"message": (tr(lang, f"Готово, шагов выполнено: {done}", f"Done, {done} step(s) completed") if not failed
                            else tr(lang, f"Ошибок: {len(failed)} из {len(report)} шагов", f"{len(failed)} of {len(report)} steps failed")),
                "title": command["name"], "kind": "routine", "steps": report, "status": "done" if not failed else "error",
                "speak": " ".join(s.get("say", "") for s in report if s.get("say")).strip() or None,
                "answer": "\n\n".join(s.get("answer", "") for s in report if s.get("answer")).strip() or None}

    async def run_routine(self, command: Dict[str, Any], *, lang: str = "ru", depth: int = 1) -> List[Dict[str, Any]]:
        from .safe_mode import is_safe_mode
        if is_safe_mode():
            raise ActionError("Безопасный режим: свои сценарии не выполняются", "safe_mode",
                              "Safe mode: custom routines do not run")
        report: List[Dict[str, Any]] = []
        now = self.now_fn()
        for index, step in enumerate(command.get("steps") or []):
            entry: Dict[str, Any] = {"index": index, "type": step["type"], "status": "done", "message": ""}
            report.append(entry)
            try:
                if not await self._when_ok(step.get("when"), now):
                    entry.update(status="skipped", message=tr(lang, "условие не выполнено", "condition not met"))
                    continue
                kind = step["type"]
                if kind == "action":
                    entry["title"] = catalog.title(step["action"], lang)
                    card = await self.run(step["action"], step.get("params") or {}, source="routine", lang=lang,
                                          confirmed=True, depth=depth, emit=False)
                    entry["status"] = "done" if card["status"] == "done" else "error" if card["status"] in ("error", "unsupported", "disabled") else card["status"]
                    entry["message"] = card.get("message", "")
                    if card.get("images"):
                        entry["images"] = card["images"]
                    if card.get("speak"):
                        entry["say"] = card["speak"]
                elif kind == "wait":
                    entry["title"] = tr(lang, "Пауза", "Wait")
                    await asyncio.sleep(step["seconds"])
                    entry["message"] = f"{step['seconds']:g} s"
                elif kind == "say" or kind == "notify":
                    entry["title"] = tr(lang, "Сказать", "Say") if kind == "say" else tr(lang, "Уведомление", "Notification")
                    entry["message"] = step["text"]
                    entry["say"] = step["text"]
                    if kind == "notify":
                        from .desktop_notifications import show_completion
                        await self._blocking(show_completion, "MemuAI · " + command["name"], step["text"])
                elif kind == "ask_ai":
                    entry["title"] = tr(lang, "Спросить ИИ", "Ask AI")
                    answer = await self._ask_ai(step["prompt"], lang)
                    entry["message"] = answer[:400]
                    entry["answer"] = answer
                    if step.get("speak"):
                        entry["say"] = answer[:1200]
                elif kind == "layout":
                    entry["title"] = tr(lang, "Раскладка окон", "Window layout")
                    result = await self.restore_layout(step["layout"], launch=step.get("launch", True),
                                                       timeout=step.get("timeout", 25), lang=lang)
                    entry["message"] = tr(lang, f"окон на месте: {result['placed']} из {result['total']}", f"{result['placed']} of {result['total']} windows placed")
                    entry["notes"] = result["notes"]
                    if result["placed"] < result["total"]:
                        entry["status"] = "partial"
            except (ActionError, asyncio.TimeoutError) as exc:
                entry.update(status="error", message=(exc.text(lang) if isinstance(exc, ActionError) else str(exc))
                             or type(exc).__name__)
            except Exception as exc:
                log.exception("routine step failed")
                entry.update(status="error", message=f"{type(exc).__name__}: {exc}"[:300])
            if entry["status"] == "error" and not step.get("continue_on_error", True):
                break
        return report

    async def _when_ok(self, when: Optional[Dict[str, Any]], now: datetime) -> bool:
        if not when:
            return True
        if when.get("weekdays") and now.isoweekday() not in when["weekdays"]:
            return False
        if when.get("time_from") or when.get("time_to"):
            minute = now.hour * 60 + now.minute
            a = int(when.get("time_from", "00:00")[:2]) * 60 + int(when.get("time_from", "00:00")[3:])
            b = int(when.get("time_to", "23:59")[:2]) * 60 + int(when.get("time_to", "23:59")[3:])
            inside = a <= minute <= b if a <= b else (minute >= a or minute <= b)
            if not inside:
                return False
        if when.get("app_running") or when.get("app_not_running"):
            try:
                running = bool(await self._blocking(self._windows_of, when.get("app_running") or when.get("app_not_running")))
            except ActionError:
                running = False
            if when.get("app_running") and not running:
                return False
            if when.get("app_not_running") and running:
                return False
        return True

    async def _ask_ai(self, prompt: str, lang: str, system: str = "") -> str:
        """One model answer for a routine step or a text tool (translate, summarize, YouTube), reasoning removed."""
        from .pgdata import load_preferences
        prefs = load_preferences()
        system = (system or "You are MemuAI Quick Ask running a step of the user's routine. Answer briefly and directly, "
                  "in the language of the request.") + " Local time: " + self.now_fn().strftime("%Y-%m-%d %H:%M (%A)") + "."
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]

        from . import quick_ask_endpoint as endpoint
        if endpoint.active(prefs):
            model = str(prefs.get("quick_ask_endpoint_model") or "")
            if not model:
                raise ActionError(tr(lang, "Выберите модель своего эндпоинта", "Choose a model of your endpoint"), "no_model")
            try:
                text = await asyncio.wait_for(endpoint.complete(endpoint.normalize_url(prefs.get("quick_ask_endpoint_url")),
                                                                endpoint.get_key(), model, messages, lang=lang), 120)
            except RuntimeError as exc:
                raise ActionError(str(exc), "model") from exc
            return clean_ai_text(text)
        if self.completion is None:
            raise ActionError(tr(lang, "Модель недоступна", "No model available"), "no_model")
        model = str(prefs.get("quick_ask_model") or prefs.get("default_model") or "")
        if not model:
            raise ActionError(tr(lang, "Выберите модель быстрого доступа", "Choose a Quick Ask model"), "no_model")
        text = await asyncio.wait_for(self.completion(model, messages), 120)
        return clean_ai_text(text)

    async def _hide_quick(self) -> None:
        """Actions on «the current window» must not hit the Quick Ask popup itself."""
        manager = self.quick_manager()
        if manager is None or not getattr(manager, "hwnd", None):
            return
        try:
            visible = bool(getattr(manager, "visible", True))
            if visible:
                await self._blocking(manager.hide, True)
                await asyncio.sleep(0.25)
        except Exception:
            pass

def phase_name(phase: Any, lang: str) -> str:
    return {"work": tr(lang, "работа", "focus"), "short_break": tr(lang, "короткий перерыв", "short break"),
            "long_break": tr(lang, "длинный перерыв", "long break")}.get(str(phase or ""), str(phase or ""))

CONFIRM_TEXT = {
    "power.shutdown": ("Выключить компьютер? Несохранённая работа в открытых программах может пропасть.",
                       "Shut the computer down? Unsaved work in open programs may be lost."),
    "power.restart": ("Перезагрузить компьютер? Несохранённая работа в открытых программах может пропасть.",
                      "Restart the computer? Unsaved work in open programs may be lost."),
    "power.signout": ("Выйти из учётной записи Windows? Все программы закроются.", "Sign out of Windows? Every program will close."),
    "power.sleep": ("Перевести компьютер в спящий режим?", "Put the computer to sleep?"),
    "power.hibernate": ("Перевести компьютер в гибернацию?", "Hibernate the computer?"),
    "recycle.empty": ("Безвозвратно удалить всё из корзины?", "Permanently delete everything in the Recycle Bin?"),
}

def describe(action: str, params: Dict[str, Any], lang: str, engine: Optional[Engine] = None) -> str:
    """One line for the confirmation card."""
    en = lang == "en"
    if action in ("power.shutdown", "power.restart") and params.get("delay_minutes"):
        span = intents.duration_text(int(params["delay_minutes"]) * 60, lang)
        if action == "power.shutdown":
            return f"Shut the computer down in {span}?" if en else f"Выключить компьютер через {span}?"
        return f"Restart the computer in {span}?" if en else f"Перезагрузить компьютер через {span}?"
    if action in CONFIRM_TEXT:
        return CONFIRM_TEXT[action][1 if lang == "en" else 0]
    if action == "computer.use":
        task = str(params.get("task") or "").strip()
        short = task[:160] + ("…" if len(task) > 160 else "")
        return (f"Let the AI use the mouse and keyboard for: “{short}”? Every step asks first; Esc stops it." if en
                else f"Дать ИИ мышь и клавиатуру для задачи «{short}»? Каждый шаг спросит разрешения; Esc — остановить.")
    if action == "app.close":
        return (f"Close {params.get('name')}? The app itself asks about unsaved changes." if en
                else f"Закрыть {params.get('name')}? О несохранённых изменениях программа спросит сама.")
    if action == "process.kill":
        what = params.get("name") or f"PID {params.get('pid')}"
        return f"Force-end {what}? Its unsaved data will be lost." if en else f"Принудительно завершить {what}? Несохранённые данные пропадут."
    if action == "type.text":
        text = str(params.get("text") or "")
        short = text[:80] + ("…" if len(text) > 80 else "")
        return f"Type into the active window: “{short}”?" if en else f"Напечатать в активном окне: «{short}»?"
    if action == "keys.press":
        return f"Press {params.get('keys')} in the active window?" if en else f"Нажать {params.get('keys')} в активном окне?"
    if action == "wifi.set":
        return ("Turn Wi‑Fi off? The internet connection may drop." if en else "Выключить Wi‑Fi? Интернет может пропасть.") \
            if not params.get("on") else ("Turn Wi‑Fi on?" if en else "Включить Wi‑Fi?")
    if action == "shell.run":

        command = re.sub(r"\s+", " ", str(params.get("command") or "")).strip()
        hidden = len(command) - 300
        shown = command[:300] + ((f"… (+{hidden} more characters)" if en else f"… (ещё {hidden} симв.)") if hidden > 0 else "")
        return ("Run PowerShell: " if en else "Выполнить PowerShell: ") + shown
    if action == "open.path":
        return ("Run this program: " if en else "Запустить программу: ") + str(params.get("path") or "")
    title = catalog.title(action, lang)
    if action in MODEL_CLIPBOARD_TEXT and not str(params.get("text") or "").strip():
        return title + (": the text from the clipboard (the AI will read it)" if en else ": текст из буфера обмена (его прочитает ИИ)")
    if action == "command.run" and engine is not None:
        command = engine._find_command(params.get("id") or params.get("name"))
        if command:
            parts = [catalog.title(s["action"], lang) if s["type"] == "action" else s["type"] for s in command["steps"][:6]]
            return f"«{command['name']}»: " + ", ".join(parts)
    shown = ", ".join(f"{k}: {str(v)[:60]}" for k, v in params.items() if k not in ("id",))
    return title + (f" ({shown})" if shown else "")

def _audit_params(action: str, params: Any) -> Any:
    if not isinstance(params, dict):
        return None
    private = _private_actions()
    out = {}
    for key, value in params.items():
        if action in private and key == "text":
            out[key] = "…"
            continue
        text = str(value)
        if key in ("text", "command", "prompt") and len(text) > 80:
            text = text[:80] + "…"
        out[key] = text[:200]
    return out

def _private_actions() -> set:
    """Actions whose free text is private (Round 24/25 ``NO_AUDIT_PARAMS``)."""
    import importlib
    out: set = set()
    for name in ("assistant_r24", "assistant_r25"):
        try:
            out |= set(getattr(importlib.import_module("." + name, __package__), "NO_AUDIT_PARAMS", ()))
        except Exception:
            pass
    return out

def help_groups(lang: str) -> List[Dict[str, Any]]:
    en = lang == "en"
    rows = [
        ("apps", ["open Telegram", "close Chrome", "switch to VS Code", "open Downloads", "open sound settings"] if en else
         ["открой телеграм", "закрой хром", "переключись на vs code", "открой загрузки", "открой настройки звука"]),
        ("sound", ["volume 30", "louder", "mute", "next track", "pause"] if en else ["громкость 30", "громче", "выключи звук", "следующий трек", "пауза"]),
        ("display", ["brightness 60", "dark mode", "night light"] if en else ["яркость 60", "тёмная тема", "ночной свет"]),
        ("windows", ["maximize", "snap left", "move to the other monitor", "tile windows", "save layout work"] if en else
         ["разверни окно", "окно влево", "перенеси окно на другой монитор", "расставь окна сеткой", "сохрани раскладку работа"]),
        ("time", ["timer 10 minutes for tea", "alarm at 7:30", "start pomodoro", "remind me in telegram at 15:00 to call mom",
                  "schedule every weekday at 9 an AI news digest"] if en else
         ["таймер 10 минут для чая", "будильник на 7:30", "запусти помодоро", "напомни в телеграме в 15:00 позвонить маме",
          "запланируй по будням в 9 сводку новостей ИИ"]),
        ("system", ["battery", "my ip", "screenshot", "empty the recycle bin", "lock"] if en else
         ["сколько заряда", "мой ip", "скриншот", "очисти корзину", "заблокируй"]),
        ("web", ["order sushi", "weather tomorrow", "100 usd to eur", "play despacito on youtube"] if en else
         ["закажи роллы", "погода завтра", "100 usd в рублях", "включи despacito на ютубе"]),
        ("memuai", ["draw a cat in space", "note: buy milk", "open MemuAI panels"] if en else
         ["нарисуй кота в космосе", "заметка купить молоко", "открой панели в memuai"]),
        ("power", ["shut down the computer", "restart", "sleep"] if en else ["выключи компьютер", "перезагрузи", "спящий режим"]),
        ("tools", ["#ff8800 to rgb", "base64 decode aGVsbG8=", "format json clipboard", "sha256 hello", "how many days until new year",
                   "time in Tokyo", "15:00 London to New York", "roll 2d6", "lorem ipsum 3 paragraphs"] if en else
         ["#ff8800 в rgb", "декодируй base64 aGVsbG8=", "отформатируй json из буфера", "sha256 привет", "сколько дней до нового года",
          "время в Токио", "15:00 по Москве в Нью-Йорке", "брось 2 кубика", "lorem ipsum 3 абзаца"]),
    ]
    cats = catalog.categories(lang)
    return [{"id": key, "title": cats[key], "examples": examples} for key, examples in rows]

REMINDER_PREFIX = "asst-rem-"

def telegram_status() -> Dict[str, Any]:
    try:
        from .pgdata import load_preferences
        prefs = load_preferences()
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
    if not prefs.get("telegram_enabled"):
        return {"ok": False, "reason": "telegram_enabled=false"}
    if not str(prefs.get("telegram_bot_token") or "").strip():
        return {"ok": False, "reason": "no token"}
    if not str(prefs.get("telegram_chat_id") or "").strip():
        return {"ok": False, "reason": "not paired"}
    return {"ok": True}

def telegram_problem(status: Dict[str, Any], lang: str) -> str:
    """Why a Telegram reminder cannot be sent, for the user (Round 26: the card used to end with the internal
    reason code — «(telegram_enabled=false)», «(no token)» — or a raw exception text)."""
    reason = str(status.get("reason") or "")
    if reason == "telegram_enabled=false":
        return tr(lang, "Telegram-бот MemuAI выключен: включите его в Настройки → Telegram.",
                  "The MemuAI Telegram bot is turned off: turn it on in Settings → Telegram.")
    if reason == "no token":
        return tr(lang, "У Telegram-бота MemuAI нет токена: Настройки → Telegram.",
                  "The MemuAI Telegram bot has no token: Settings → Telegram.")
    return tr(lang, "Telegram-бот MemuAI не подключён: Настройки → Telegram.",
              "The MemuAI Telegram bot is not paired: Settings → Telegram.")

def add_reminder_task(text: str, when: datetime, lang: str = "ru") -> str:
    """A one-shot task in schedules.json with ``kind: "reminder"`` — ``scheduler._run_task`` delivers it
    without any model call (see :func:`deliver_reminder`). Visible and editable in «Планирование»."""
    from .pgdata import _mutate_schedules
    task_id = REMINDER_PREFIX + uuid.uuid4().hex[:10]
    task = {"id": task_id, "name": (("Reminder: " if lang == "en" else "Напоминание: ") + text)[:80],
            "prompt": text[:2000], "model": "", "mode": "chat", "kind": "reminder", "reminder_text": text[:2000],
            "run_at": when.replace(second=0, microsecond=0).isoformat(), "enabled": True,
            "notify_telegram": True, "notify_email": False, "created_by": "quick-ask", "created": int(time.time()),
            "lang": "en" if lang == "en" else "ru"}

    def add(tasks: List[Dict[str, Any]]) -> bool:
        tasks.append(task)
        return True
    _mutate_schedules(add)
    return task_id

def remove_reminder_task(ref: str, match_text: bool = False) -> int:
    from .pgdata import _mutate_schedules
    removed = []
    low = str(ref or "").lower().strip()

    def drop(tasks: List[Dict[str, Any]]) -> bool:
        keep = []
        for task in tasks:
            hit = task.get("kind") == "reminder" and (task.get("id") == ref or (
                match_text and low and low in str(task.get("reminder_text") or "").lower()))
            (removed if hit else keep).append(task)
        tasks[:] = keep
        return bool(removed)
    if not low:
        return 0
    _mutate_schedules(drop)
    return len(removed)

def list_reminder_tasks() -> List[Dict[str, Any]]:
    try:
        from .pgdata import load_schedules
        tasks = load_schedules()
    except Exception:
        return []
    out = []
    for task in tasks:
        if task.get("kind") == "reminder" and task.get("enabled", True):
            try:

                moment = datetime.fromisoformat(str(task.get("run_at")).replace("Z", "+00:00"))
                if moment.tzinfo is not None:
                    moment = moment.astimezone().replace(tzinfo=None)
                when = moment.strftime("%d.%m %H:%M")
            except ValueError:
                when = str(task.get("run_at"))
            out.append({"id": task.get("id"), "when": when, "text": task.get("reminder_text") or task.get("prompt") or ""})
    return out

async def deliver_reminder(task: Dict[str, Any]) -> Tuple[bool, str]:
    """Called by ``scheduler._run_task`` (MemuAI Proxy process) for ``kind == "reminder"``.

    Sends the reminder text straight to the paired Telegram chat (no model, no notify-mode
    filtering: the user explicitly asked for a Telegram reminder; quiet hours still apply)
    and shows a Windows notification."""
    text = str(task.get("reminder_text") or task.get("prompt") or "").strip() or "⏰"
    lang = "en" if task.get("lang") == "en" else "ru"
    ok, info = False, ""
    try:
        from .pgdata import load_preferences
        prefs = load_preferences()
        token = str(prefs.get("telegram_bot_token") or "").strip()
        chat_id = str(prefs.get("telegram_chat_id") or "").strip()
        if task.get("notify_telegram", True) and prefs.get("telegram_enabled") and token and chat_id:
            import httpx
            from . import telegram_bot
            quiet = False
            try:
                quiet = bool(telegram_bot._in_quiet_hours(prefs))
            except Exception:
                quiet = False
            body = ("⏰ **" + ("Reminder" if lang == "en" else "Напоминание") + "**\n\n" + text)
            async with httpx.AsyncClient() as client:
                await telegram_bot._send(client, token, chat_id, body, None, markdown=True, silent=quiet)
            ok, info = True, "telegram"
        else:
            info = "telegram is not paired"
    except Exception as exc:
        info = f"telegram: {exc}"[:300]
    try:
        from .desktop_notifications import show_completion
        await asyncio.to_thread(show_completion, "MemuAI · " + ("Reminder" if lang == "en" else "Напоминание"), text, sound=True)
    except Exception:
        pass
    return ok, info

NOTES_CHAT_ID = "quick-notes"

def add_note(text: str, lang: str = "ru") -> int:
    from .pgdata import load_chats, upsert_chat
    now = int(time.time() * 1000)
    chats = load_chats()
    row = next((chat for chat in chats if chat.get("id") == NOTES_CHAT_ID), None)
    message = {"role": "user", "content": str(text)[:20000], "createdAt": now, "note": True}
    if row:
        row.setdefault("messages", []).append(message)
        row["updated"] = now
    else:
        row = {"id": NOTES_CHAT_ID, "title": "Notes" if lang == "en" else "Заметки", "mode": "chat",
               "messages": [message], "created": now, "updated": now, "pinned": True}
    upsert_chat(row)
    return now

def remove_note(created: int) -> bool:
    from .pgdata import load_chats, upsert_chat
    chats = load_chats()
    row = next((chat for chat in chats if chat.get("id") == NOTES_CHAT_ID), None)
    if not row:
        return False
    before = len(row.get("messages") or [])
    row["messages"] = [m for m in row.get("messages") or [] if m.get("createdAt") != created]
    row["updated"] = int(time.time() * 1000) + 1
    upsert_chat(row)
    return len(row["messages"]) != before

VOICE_PREF_MAP = {"stt_provider": "voice_stt_provider", "stt_local_model": "voice_stt_local_model",
                  "stt_api_model": "voice_stt_api_model", "tts_provider": "voice_tts_provider", "tts_voice": "voice_tts_voice",
                  "tts_local_model": "voice_tts_local_model", "tts_api_voice": "voice_tts_api_voice",
                  "language": "voice_language", "tts_rate": "voice_tts_rate"}

def voice_prefs(prefs: Dict[str, Any], body: Any, store: Optional[Store] = None) -> Dict[str, Any]:
    """Preferences for a voice request; Quick Ask (``body.quick``) overlays its own non-empty choices.

    Only the keys above can change, and only from the assistant's own settings file — the page
    cannot inject arbitrary preferences (e.g. API URLs)."""
    if not isinstance(body, dict) or not body.get("quick"):
        return prefs
    try:
        voice = (store or Store()).load()["settings"]["voice"]
    except Exception:
        return prefs
    out = dict(prefs)
    for key, pref in VOICE_PREF_MAP.items():
        value = voice.get(key)
        if value not in (None, ""):
            out[pref] = value

    if voice.get("tts_api_voice") and out.get("voice_tts_provider") == "local":
        out["voice_tts_local_voice"] = voice["tts_api_voice"]
        out["voice_tts_api_voice"] = prefs.get("voice_tts_api_voice", "")
    if not voice.get("stt_postprocess"):
        out["voice_postprocess_enabled"] = False
    return out

TOOL_NAME = "assistant_action"

def tool_prompt(settings: Dict[str, Any], lang: str = "ru") -> str:
    enabled = [a for a in catalog.BY_ID if a not in settings["disabled_actions"]
               and (settings["advanced_shell"] or not catalog.BY_ID[a]["advanced"])]
    return (
        f"  {TOOL_NAME} {{action, params}} — control the user's Windows PC and MemuAI (open/close apps, volume, "
        "brightness, windows, timers, reminders incl. Telegram, Pomodoro, screenshots, images, notes, power…). "
        "Use it when the user ASKS you to DO something, not to explain how. One call per action; several actions "
        "may go in one block. Actions marked [confirm]/[dangerous] show the user a confirmation button — never "
        "claim they are done until the result says so. Reminder times: local 'YYYY-MM-DDTHH:MM' or in_minutes. "
        "Never order, pay or book anything: food/shop/taxi actions only open the site with a search.\n"
        "  Actions:\n" + catalog.compact_catalog(enabled))

def tool_schema() -> Dict[str, Any]:
    return {"type": "function", "function": {
        "name": TOOL_NAME,
        "description": "Run a MemuAI assistant action on the user's PC (see the action list in the system prompt).",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": list(catalog.BY_ID)},
            "params": {"type": "object", "description": "Parameters of the action"}},
            "required": ["action"]}}}

def model_result(card: Dict[str, Any]) -> Dict[str, Any]:
    """What the model sees after a tool call (short, honest about confirmation)."""
    out = {"tool": TOOL_NAME, "action": card.get("action"), "status": card.get("status"),
           "message": str(card.get("message") or "")[:1500]}
    if card.get("status") == "confirm":
        out["note"] = ("Waiting for the user to press Confirm on the card. Tell the user to confirm; do NOT say it is done.")
    if card.get("details"):
        out["details"] = card["details"][:12]
    if card.get("list"):
        out["list"] = card["list"][:30]
    if card.get("images"):
        out["images"] = card["images"][:4]
    if card.get("answer"):
        out["answer"] = str(card["answer"])[:3000]
    return out

_ENGINE: Optional[Engine] = None
_ENGINE_LOCK = threading.Lock()

def engine() -> Engine:
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = Engine()
        return _ENGINE

def set_engine(value: Optional[Engine]) -> None:
    global _ENGINE
    with _ENGINE_LOCK:
        _ENGINE = value

class HotkeyBinds:
    """Global hotkeys of custom commands — one ``win_hotkey.HotkeyListener`` (own thread) per bind."""

    def __init__(self, eng: Engine) -> None:
        self.engine = eng
        self.listeners: Dict[str, Any] = {}
        self.status: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()

    def sync(self, commands: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        if sys.platform != "win32":
            return {c["id"]: {"ok": False, "code": "unsupported"} for c in commands if c.get("hotkey")}
        from .win_hotkey import HotkeyListener
        wanted = {c["id"]: c for c in commands if c.get("hotkey") and c.get("enabled", True)}
        with self.lock:
            for command_id in list(self.listeners):
                listener = self.listeners[command_id]
                if command_id not in wanted or wanted[command_id]["hotkey"] != listener.active:
                    try:
                        listener.clear()
                        listener.stop()
                    except Exception:
                        pass
                    self.listeners.pop(command_id, None)
                    self.status.pop(command_id, None)
            for command_id, command in list(wanted.items())[:24]:
                if command_id in self.listeners:
                    continue
                listener = HotkeyListener(lambda cid=command_id: self._fire(cid))
                result = listener.set_hotkey(command["hotkey"])
                self.status[command_id] = result
                if result.get("ok"):
                    self.listeners[command_id] = listener
                else:
                    try:
                        listener.stop()
                    except Exception:
                        pass
        return dict(self.status)

    def _fire(self, command_id: str) -> None:
        eng = self.engine
        coro = eng.run("command.run", {"id": command_id}, source="hotkey", lang=eng._lang(), emit=True)
        loop = eng.loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, loop)
        else:
            asyncio.run(coro)

    def stop(self) -> None:
        with self.lock:
            for listener in self.listeners.values():
                try:
                    listener.clear()
                    listener.stop()
                except Exception:
                    pass
            self.listeners.clear()

def register_quick_tools(eng: Engine) -> bool:
    """Plug into the Quick Ask extension contract: ``quick_ask_api.register_tool`` (Round 22 core).

    The handler waits for the action up to ``TOOL_WAIT`` seconds (the core gives a tool 60 s);
    a slower one (image generation, a layout with app launches) keeps running and its card
    arrives through ``/api/assistant/events``."""
    try:
        from . import quick_ask_api
    except Exception:
        return False
    register_tool = getattr(quick_ask_api, "register_tool", None)
    if register_tool is None:
        return False

    async def handler(args: Any = None, ctx: Any = None, **_kw: Any) -> Dict[str, Any]:
        args = args if isinstance(args, dict) else {}
        action = str(args.get("action") or "")
        if not action and args.get("tool"):
            action = catalog.tool_name_to_action(str(args["tool"]))
        params = args.get("params") if isinstance(args.get("params"), dict) else {
            k: v for k, v in args.items() if k not in ("action", "tool")}
        lang = "en" if isinstance(ctx, dict) and ctx.get("lang") == "en" else "ru"
        settings = eng.settings()
        if not settings["model_tools"] or not settings["enabled"]:
            return {"error": "Assistant actions are turned off in Quick Ask settings."}
        job = asyncio.ensure_future(eng.run(action, params, source="model", lang=lang, emit=True))
        done, _pending = await asyncio.wait({job}, timeout=TOOL_WAIT)
        if not done:
            return {"action": action, "status": "running",
                    "message": "Still running; the result will appear on a card in the window. Tell the user it is in progress."}
        card = job.result()
        out = model_result(card)
        out.pop("tool", None)
        out["ui"] = card
        return out

    def label(args: Any = None, lang: str = "ru", **_kw: Any) -> str:
        action = str((args or {}).get("action") or "") if isinstance(args, dict) else ""
        return catalog.title(action, lang) if action in catalog.BY_ID else ("Команда" if lang != "en" else "Command")

    def enabled(_settings: Any = None) -> bool:
        try:
            current = eng.settings()
        except Exception:
            return False
        return bool(current["enabled"] and current["model_tools"])

    def description(_settings: Any = None) -> str:

        try:
            current = eng.settings()
        except Exception:
            current = normalize_settings({})
        return tool_prompt(current, "en").strip()[len(TOOL_NAME):].strip()

    try:
        register_tool(TOOL_NAME, description, handler, label=label, enabled=enabled)
    except Exception as exc:
        log.warning("assistant tool not registered: %s", exc)
        return False
    return True

TOOL_WAIT = 50.0

def register(app, *, proxy_sse=None, proxy_json=None, completion=None) -> Engine:
    """Routes ``/api/assistant/*`` (called from ``playground_api.create_app`` before the static mount)."""
    from fastapi.responses import JSONResponse

    eng = engine()
    eng.proxy_sse, eng.proxy_json, eng.completion = proxy_sse, proxy_json, completion
    eng.app_state = app.state
    eng.quick_manager = lambda: getattr(app.state, "quick_ask", None)
    binds = HotkeyBinds(eng)
    app.state.assistant = eng
    app.state.assistant_binds = binds
    register_quick_tools(eng)

    def capture_loop() -> None:
        if eng.loop is None:
            try:
                eng.loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

    async def body_of(request: Request) -> Dict[str, Any]:
        capture_loop()
        try:
            data = await request.json()
        except Exception:
            data = {}
        return data if isinstance(data, dict) else {}

    def lang_of(body: Dict[str, Any]) -> str:
        return "en" if body.get("lang") == "en" else "ru"

    @app.get("/api/assistant/state")
    async def assistant_state(after: int = 0):
        capture_loop()
        data = await asyncio.to_thread(eng.store.load)
        snap = await asyncio.to_thread(eng.timers.snapshot)
        return {"timers": snap, "events": eng.events_after(int(after or 0)), "seq": eng.seq,
                "settings": data["settings"], "platform": sys.platform,
                "commands": [{"id": c["id"], "name": c["name"], "enabled": c["enabled"], "hotkey": c["hotkey"]} for c in data["commands"]],
                "hotkeys": binds.status}

    @app.get("/api/assistant/events")
    async def assistant_events(after: int = 0):
        capture_loop()
        return {"events": eng.events_after(int(after or 0)), "seq": eng.seq}

    @app.post("/api/assistant/match")
    async def assistant_match(request: Request):
        body = await body_of(request)
        found = await asyncio.to_thread(eng.match, str(body.get("text") or ""), lang_of(body))
        return {"match": found}

    async def route_ai(text: str, lang: str, soft_miss: bool = False) -> Optional[Dict[str, Any]]:
        """Round 33 (r33-quick): the AI router. ``None`` → as before; ``{"card": None, "router"}`` → the chat answers;
        ``{"card", "match", "router"}`` → the action ran through the safe layer (source «router»)."""
        from . import assistant_router
        try:
            decision = await assistant_router.route(eng, text, lang, soft_miss=soft_miss)
        except Exception:
            log.exception("assistant router")
            return None
        if not decision:
            return None
        info = {"model": decision.get("model", ""), "ms": decision.get("ms", 0), "action": decision["action"],
                **({"reason": decision["reason"]} if decision.get("reason") else {})}
        if decision["action"] == "chat":
            return {"card": None, "match": None, "router": info}
        if not await eng.precheck({"action": decision["action"], "params": decision["params"]}):
            info["reason"] = "not_running"
            return {"card": None, "match": None, "router": info}
        card = await eng.run(decision["action"], decision["params"], source="router", lang=lang, emit=False)
        card["router"] = info
        if card["status"] in ("error", "unsupported") and card.get("code") in ("not_found", "not_running", "bad_params", "unknown"):
            card["fallback"] = True
        return {"card": card, "match": {"action": decision["action"], "params": decision["params"], "rule": "ai-router"},
                "router": info}

    @app.post("/api/assistant/run")
    async def assistant_run(request: Request):
        body = await body_of(request)
        action = str(body.get("action") or "")
        text = str(body.get("text") or "")
        lang = lang_of(body)
        if not action and text:
            conversation = bool(body.get("in_chat") or body.get("conversation"))
            found = await asyncio.to_thread(eng.match, text, lang, conversation)
            if not found:
                routed = await route_ai(text, lang)
                return routed or {"card": None, "match": None}
            if not await eng.precheck(found):
                routed = await route_ai(text, lang, soft_miss=True)
                if routed and routed.get("card"):
                    return routed
                return {"card": {"action": found["action"], "status": "error", "code": "not_running", "fallback": True,
                                 "message": "", "title": found.get("title", "")}, "match": found,
                        **({"router": routed["router"]} if routed else {})}
            action, params = found["action"], found["params"]
            if found.get("action") == "command.run":
                params = found["params"]
            card = await eng.run(action, params, source="local", lang=lang)
            if card["status"] == "error" and found.get("fallback_on_miss") and card.get("code") in ("not_found", "not_running"):
                card["fallback"] = True
            return {"card": card, "match": found}
        source = body.get("source") if body.get("source") in ("local", "settings", "test") else "local"
        card = await eng.run(action, body.get("params") or {}, source=source, lang=lang)
        return {"card": card}

    @app.post("/api/assistant/confirm")
    async def assistant_confirm(request: Request):
        body = await body_of(request)
        card = await eng.confirm(str(body.get("token") or ""), bool(body.get("accept")), lang_of(body))
        return {"card": card}

    @app.post("/api/assistant/undo")
    async def assistant_undo(request: Request):
        body = await body_of(request)
        return {"card": await eng.undo(str(body.get("undo") or ""), lang_of(body))}

    @app.post("/api/assistant/timers")
    async def assistant_timers(request: Request):
        """Direct timer controls of the plugin UI (no audit noise): dismiss/snooze/pause/resume/cancel/stopwatch/pomodoro."""
        body = await body_of(request)
        op = str(body.get("op") or "")
        ref = str(body.get("id") or "")
        t = eng.timers
        if op == "dismiss":
            t.dismiss(ref)
            eng.stop_ring()
        elif op == "snooze":
            minutes = _finite(body.get("minutes"), 5.0)
            if minutes is None or not 0 < minutes <= 24 * 60:
                return JSONResponse({"error": "minutes: 1…1440"}, 400)
            t.snooze(ref, minutes)
            eng.stop_ring()
        elif op == "pause":
            t.pause(ref, resume=False)
        elif op == "resume":
            t.pause(ref, resume=True)
        elif op == "cancel":
            t.cancel(ref, kind=None)
        elif op == "add":
            seconds = _finite(body.get("seconds"), 60.0)
            if seconds is None or not 0 < seconds <= 7 * 86400:
                return JSONResponse({"error": "seconds: 1…604800"}, 400)
            try:
                t.add_timer(seconds, str(body.get("label") or ""))
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, 400)
        elif op == "stopwatch":
            t.stopwatch(str(body.get("action") or "start"))
        elif op == "pomodoro":
            t.pomodoro(str(body.get("action") or "start"), eng.settings()["pomodoro"])
        else:
            return JSONResponse({"error": "unknown op"}, 400)
        return {"ok": True, "timers": t.snapshot()}

    @app.get("/api/assistant/config")
    async def assistant_config(lang: str = "ru"):
        capture_loop()
        data = await asyncio.to_thread(eng.store.load)
        return {"settings": data["settings"], "defaults": DEFAULT_SETTINGS, "commands": data["commands"],
                "layouts": {k: {"name": k, "windows": len(v["windows"]), "captured_at": v.get("captured_at"),
                                "apps": [w["app"]["name"] or w["app"]["exe"] for w in v["windows"]][:12]}
                            for k, v in data["layouts"].items()},
                "catalog": catalog.public(lang), "categories": catalog.categories(lang), "help": help_groups(lang),
                "template": template_good_morning(lang), "hotkeys": binds.status, "platform": sys.platform,
                "telegram": telegram_status()}

    @app.post("/api/assistant/settings")
    async def assistant_settings(request: Request):
        body = await body_of(request)
        patch = body.get("settings") if isinstance(body.get("settings"), dict) else {}

        def apply(data):
            merged = dict(data["settings"])
            for key, value in patch.items():
                if key in ("voice", "pomodoro") and isinstance(value, dict):
                    merged[key] = {**merged.get(key, {}), **value}
                else:
                    merged[key] = value
            data["settings"] = normalize_settings(merged)
        data = await asyncio.to_thread(eng.store.update, apply)
        return {"ok": True, "settings": data["settings"]}

    @app.post("/api/assistant/commands")
    async def assistant_commands(request: Request):
        """op: save {command} | delete {id} | import {commands, replace} | test {command} | toggle {id, enabled}."""
        body = await body_of(request)
        op = str(body.get("op") or "save")
        lang = lang_of(body)
        try:
            if op == "save":
                command = validate_command(body.get("command"))
                existing = {}

                def save(data):
                    nonlocal existing
                    others = [c for c in data["commands"] if c["id"] != command["id"]]
                    hotkey_conflict(command, others, lang)
                    existing = next((c for c in data["commands"] if c["id"] == command["id"]), {})
                    if existing:
                        command["created"] = existing.get("created", command["created"])
                    if not existing and len(others) >= MAX_COMMANDS:
                        raise CommandError(f"max {MAX_COMMANDS} commands")
                    data["commands"] = others + [command] if not existing else [command if c["id"] == command["id"] else c for c in data["commands"]]
                data = await asyncio.to_thread(eng.store.update, save)
            elif op == "delete":
                ref = str(body.get("id") or "")
                data = await asyncio.to_thread(eng.store.update, lambda d: d.__setitem__("commands", [c for c in d["commands"] if c["id"] != ref]))
            elif op == "toggle":
                ref = str(body.get("id") or "")

                def toggle(d):
                    for c in d["commands"]:
                        if c["id"] == ref:
                            c["enabled"] = bool(body.get("enabled"))
                data = await asyncio.to_thread(eng.store.update, toggle)
            elif op == "import":
                raw = body.get("commands")
                if isinstance(raw, dict):
                    raw = raw.get("commands")
                if not isinstance(raw, list):
                    raise CommandError("commands: expected a list")
                clean, errors = [], []
                for i, row in enumerate(raw[:MAX_COMMANDS]):
                    try:
                        clean.append(strip_imported_paths(validate_command(row)))
                    except CommandError as exc:
                        errors.append(f"#{i + 1}: {exc}")

                def imp(d):
                    current = [] if body.get("replace") else d["commands"]
                    ids = {c["id"] for c in clean}
                    d["commands"] = ([c for c in current if c["id"] not in ids] + clean)[:MAX_COMMANDS]
                data = await asyncio.to_thread(eng.store.update, imp)
                status = await asyncio.to_thread(binds.sync, data["commands"])
                return {"ok": True, "imported": len(clean), "errors": errors, "commands": data["commands"], "hotkeys": status}
            elif op == "test":
                command = validate_command(body.get("command"))
                tier = command_tier(command, eng._find_command)
                if tier == "dangerous":
                    return JSONResponse({"error": tr(lang, "Команда с опасными шагами запускается только фразой или клавишей — с подтверждением.",
                                                     "A command with dangerous steps runs only by phrase or hotkey, with confirmation.")}, 400)
                report = await eng.run_routine(command, lang=lang)
                return {"ok": True, "steps": report}
            elif op == "export":
                data = await asyncio.to_thread(eng.store.load)
                return {"version": 1, "app": "MemuAI", "kind": "quick-commands", "exported_at": int(time.time()),
                        "commands": data["commands"]}
            else:
                return JSONResponse({"error": "unknown op"}, 400)
        except CommandError as exc:
            return JSONResponse({"error": str(exc), "code": "invalid"}, 400)
        status = await asyncio.to_thread(binds.sync, data["commands"])
        return {"ok": True, "commands": data["commands"], "hotkeys": status}

    @app.post("/api/assistant/layout")
    async def assistant_layout(request: Request):
        """op: capture {name?, save?} → layout | delete {name} | restore {name|layout}."""
        body = await body_of(request)
        op = str(body.get("op") or "capture")
        lang = lang_of(body)
        try:
            if op == "capture":
                layout = await eng.capture_layout(str(body.get("name") or ""))
                if body.get("save"):
                    await asyncio.to_thread(eng.store.update, lambda d: d["layouts"].__setitem__(layout["name"], layout))
                manager = eng.quick_manager()
                if manager is not None and body.get("reshow", True):
                    try:
                        await asyncio.to_thread(manager.show, "api")
                    except Exception:
                        pass
                return {"ok": True, "layout": layout}
            if op == "delete":
                name = str(body.get("name") or "")
                await asyncio.to_thread(eng.store.update, lambda d: d["layouts"].pop(name, None))
                return {"ok": True}
            if op == "restore":
                layout = body.get("layout")
                if not isinstance(layout, dict):
                    layout = (await asyncio.to_thread(eng.store.load))["layouts"].get(str(body.get("name") or ""))
                if not layout:
                    return JSONResponse({"error": tr(lang, "Раскладка не найдена", "Layout not found")}, 404)
                report = await eng.restore_layout(geo.validate(layout), launch=bool(body.get("launch", True)), lang=lang)
                return {"ok": True, "report": report}
        except Unsupported as exc:
            return JSONResponse({"error": str(exc) if lang != "en" else "Window layouts work only in the MemuAI Windows app.",
                                 "code": "unsupported"}, 400)
        except (ActionError, ValueError) as exc:
            return JSONResponse({"error": exc.text(lang) if isinstance(exc, ActionError) else str(exc),
                                 "code": getattr(exc, "code", "failed")}, 400)
        return JSONResponse({"error": "unknown op"}, 400)

    @app.get("/api/assistant/audit")
    async def assistant_audit(limit: int = 200):
        return {"items": await asyncio.to_thread(eng.audit.read, max(1, min(2000, int(limit or 200))))}

    @app.post("/api/assistant/audit/clear")
    async def assistant_audit_clear():
        await asyncio.to_thread(eng.audit.clear)
        return {"ok": True}

    @app.get("/api/assistant/tools")
    async def assistant_tools():
        """Portable per-action JSON schemas (OpenAI tools format) + the single dispatcher schema."""
        return {"dispatcher": tool_schema(), "actions": catalog.openai_tools()}

    def warm() -> None:
        try:
            from . import assistant_apps
            assistant_apps.warm()
            binds.sync(eng.store.load()["commands"])
            eng.timers
        except Exception as exc:
            log.warning("assistant warm-up failed: %s", exc)
    if sys.platform == "win32" and not os.environ.get("MEMUAI_ASSISTANT_NO_WARM"):
        threading.Timer(6.0, warm).start()
    try:
        from . import assistant_r24
        assistant_r24.register(app, eng)
    except Exception as exc:
        log.warning("round 24 assistant routes not registered: %s", exc)
    return eng

try:
    from . import assistant_r24 as _r24
    _r24.install()
except Exception as _exc:  # pragma: no cover
    log.warning("round 24 assistant catalog not installed: %s", _exc)

try:
    from . import assistant_r25 as _r25
    _r25.install()
except Exception as _exc:  # pragma: no cover
    log.warning("round 25 assistant catalog not installed: %s", _exc)
