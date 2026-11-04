"""Рабочие области MemuAI: «Стандартная» и «Агентная» (компаньон).

Область — отдельный набор чатов, памяти, Work-папок, команд и задач планировщика внутри
профиля. Стандартная область живёт по старым путям (обратная совместимость: установки
без областей ничего не замечают), остальные — в ``[profiles/<pid>/]spaces/<id>/``.

Активная область записана в ``spaces.json`` профиля и действует для всех процессов
(окно MemuAI, Proxy, Telegram, CLI) — так же, как активный профиль. Память области — это
документ memory_v2 с владельцем ``<pid>--<id>``: механизм «живой файл + парковка» из
memory_v2 переключает его без копирования данных между областями.
"""

import json
import os
import re
import secrets
import shutil
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, List, Optional

STANDARD_ID = "standard"
TYPES = ("standard", "agent")
SPACE_FILES = frozenset({
    "chats.json", "schedules.json", "schedule_results.json",
    "workspaces.json", "teams.json", "team-runs.json", "panels.json",
})
# A workspace created before it had its own copy starts from the standard one's file
# (the panels the user already set up), afterwards the copies live separately.
SEED_FROM_STANDARD = frozenset({"panels.json"})
# Settings every workspace keeps for itself (``spaces/<id>/preferences.json`` over the
# shared ``preferences.json``): the assistant's character, look and models, answers,
# context, Work and MemuCode behaviour, the Live voice character, the sidebar, folders
# and own agents, and the look of the window. Keys and providers, web search, voice
# engines, messengers, notifications, hotkeys, language, light/dark mode, scale and
# motion stay shared by all workspaces.
SPACE_PREF_KEYS = frozenset({
    "ai_personality", "custom_system_prompt", "assistant_avatar", "assistant_gradient",
    "default_model", "thinking", "default_effort", "model_efforts",
    "visualizations_enabled", "visualizations_custom_enabled", "visualizations_images_enabled",
    "diagrams_mode", "product_cards_enabled", "chat_titles", "chat_titles_update",
    "context_optimization", "context_compression_threshold", "context_recent_messages",
    "default_context_limit", "context_count_images", "composer_pinned_tools",
    "voice_live_name", "voice_live_gender", "voice_live_primary_personality",
    "voice_live_secondary_personalities", "voice_live_custom_personality",
    "voice_live_personality_presets", "voice_live_personality_preset_id",
    "work_auto_apply", "work_access_mode", "work_plan_mode", "work_goal_max_turns",
    "work_plan_max_turns", "work_auto_command", "agent_models", "agent_model_notes", "agent_count",
    "code_access_mode", "code_plan_mode", "code_model", "code_auto_verify",
    "code_project_rules", "code_tool_rounds",
    "sidebar_sections", "section_order", "team_sidebar_visible", "sidebar_customize_visible",
    "chat_folders", "work_folders", "code_folders", "collapsed_folders",
    "work_collapsed_folders", "code_collapsed_folders", "memes", "chat_memes", "memes_open",
    "accent_color", "surface_dark", "surface_light", "ui_design", "ui_density", "chat_width",
    "app_font", "compact_messages", "ui_radius", "ui_glass", "ui_backdrop", "ui_msg_anim",
    "ui_msg_size", "ui_look",
})
SPACE_PREFS_FILE = "preferences.json"
MAX_SPACES = 12
STYLES = ("mirror", "warm", "playful", "calm", "direct", "witty")
EMOJI_LEVELS = ("none", "some", "lots")
GENDERS = ("f", "m", "n")
ECONOMY = ("max", "balanced", "rich")
CONTEXT_DEFAULTS: Dict[str, Any] = {
    "economy": "balanced",
    "summary_model": "",
    "embedding_model": "",
    "recall": True,
    "style_mirror": True,
    "kits_router": True,
    "followups": True,
    "bubble_pacing": True,
    "reactions": True,
    "ai_reactions": True,
}
_SPACE_ID_RE = re.compile(r"s-[a-z0-9]{6,16}")
_PROFILE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")
_PRESET_RE = re.compile(r"preset:[a-z0-9-]{1,32}")
_LOCK = threading.RLock()
_CACHE: Dict[str, tuple] = {}
# F24: deleted spaces stay in `.trash` forever — prune once per process
# (first load_state call), best-effort, never breaking the load itself.
_TRASH_MAX_AGE_SECONDS = 30 * 24 * 3600
_TRASH_MAX_BYTES = 2 * 1024 * 1024 * 1024
_TRASH_PRUNED: set = set()


def prune_space_trash(profile_id: Optional[str] = None, *,
                      max_age_seconds: int = _TRASH_MAX_AGE_SECONDS,
                      max_bytes: int = _TRASH_MAX_BYTES) -> int:
    """Delete archived (deleted) spaces older than 30 days, then the oldest
    ones while the trash is over the size cap. Returns removed entries."""
    import time as _time
    try:
        trash = _memu_dir() / profile_root(profile_id) / "spaces" / ".trash"
    except Exception:
        return 0
    if not trash.is_dir():
        return 0
    now = _time.time()
    entries = []
    for child in trash.iterdir():
        try:
            stat = child.stat()
        except OSError:
            continue
        size = 0
        if child.is_dir() and not child.is_symlink():
            for root, _dirs, files in os.walk(child):
                for name in files:
                    try:
                        size += (Path(root) / name).stat().st_size
                    except OSError:
                        pass
        else:
            size = stat.st_size
        entries.append((stat.st_mtime, size, child))
    removed = 0
    total = sum(size for _mt, size, _c in entries)
    for mtime, _size, child in sorted(entries):
        if now - mtime < max_age_seconds and total <= max_bytes:
            continue
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        except OSError:
            continue
        total -= _size
        removed += 1
    return removed
_WORKSPACE_SCOPE: ContextVar[Optional[tuple[str, str]]] = ContextVar("workspace_scope", default=None)


def _memu_dir() -> Path:
    try:
        from . import pgdata
        return Path(pgdata.MEMU_DIR)
    except Exception:  # pragma: no cover - import cycle during bootstrap
        from .config import DATA_DIR
        return Path(DATA_DIR) / "memuai"


def _now_ms() -> int:
    return int(time.time() * 1000)


def valid_space_id(value: Any) -> bool:
    return isinstance(value, str) and (value == STANDARD_ID or bool(_SPACE_ID_RE.fullmatch(value)))


def _valid_profile(value: Any) -> bool:
    return isinstance(value, str) and bool(_PROFILE_RE.fullmatch(value))


def active_profile() -> str:
    """Raw active profile id from ``profiles.json`` (no normalisation side effects)."""
    try:
        raw = json.loads((_memu_dir() / "profiles.json").read_text(encoding="utf-8"))
    except Exception:
        return "default"
    if not isinstance(raw, dict):
        return "default"
    value = str(raw.get("active_id") or "default")
    items = raw.get("items") if isinstance(raw.get("items"), list) else []
    if items and not any(isinstance(i, dict) and str(i.get("id")) == value for i in items):
        first = next((i for i in items if isinstance(i, dict) and i.get("id")), None)
        value = str(first.get("id")) if first else "default"
    return value if _valid_profile(value) else "default"


def profile_root(profile_id: Optional[str] = None) -> Path:
    """Directory of a profile's scoped data, relative to ``MEMU_DIR``."""
    pid = str(profile_id or active_profile())
    if pid == "default":
        return Path("")
    if not _valid_profile(pid):
        raise ValueError("bad profile id")
    return Path("profiles") / pid


def _state_path(profile_id: Optional[str] = None) -> Path:
    return _memu_dir() / profile_root(profile_id) / "spaces.json"


def _clean_text(value: Any, limit: int) -> str:
    text = str(value or "").replace("\x00", "").strip()
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text[:limit]


def _one_line(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def normalize_companion(raw: Any, previous: Optional[Dict[str, Any]] = None, *, touch: bool = False) -> Dict[str, Any]:
    base = dict(previous or {})
    src = raw if isinstance(raw, dict) else {}
    out: Dict[str, Any] = {}
    out["name"] = _one_line(src.get("name", base.get("name")), 40) or "Мира"
    avatar = str(src.get("avatar", base.get("avatar")) or "")
    if avatar.startswith("data:image/") and len(avatar) <= 2_000_000:
        out["avatar"] = avatar
    elif _PRESET_RE.fullmatch(avatar):
        out["avatar"] = avatar
    else:
        out["avatar"] = "preset:aurora"
    color = str(src.get("color", base.get("color")) or "")
    out["color"] = color if _COLOR_RE.fullmatch(color) else "#8b5cf6"
    gender = str(src.get("gender", base.get("gender")) or "f")
    out["gender"] = gender if gender in GENDERS else "f"
    style = str(src.get("style", base.get("style")) or "mirror")
    out["style"] = style if style in STYLES else "mirror"
    emoji = str(src.get("emoji", base.get("emoji")) or "some")
    out["emoji"] = emoji if emoji in EMOJI_LEVELS else "some"
    out["persona"] = _clean_text(src.get("persona", base.get("persona")), 1200)
    out["interests"] = _clean_text(src.get("interests", base.get("interests")), 400)
    out["looks"] = _clean_text(src.get("looks", base.get("looks")), 400)
    out["relation"] = _one_line(src.get("relation", base.get("relation")), 80)
    out["model_id"] = _one_line(src.get("model_id", base.get("model_id")), 120)
    out["voice"] = _one_line(src.get("voice", base.get("voice")), 120)
    out["created"] = int(base.get("created") or src.get("created") or _now_ms())
    out["updated"] = _now_ms() if touch else int(src.get("updated") or base.get("updated") or out["created"])
    return out


def normalize_context(raw: Any, previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = dict(CONTEXT_DEFAULTS)
    if isinstance(previous, dict):
        out.update({k: previous[k] for k in CONTEXT_DEFAULTS if k in previous})
    src = raw if isinstance(raw, dict) else {}
    for key, default in CONTEXT_DEFAULTS.items():
        if key not in src:
            continue
        value = src[key]
        if isinstance(default, bool):
            out[key] = bool(value)
        elif key == "economy":
            out[key] = value if value in ECONOMY else default
        else:
            out[key] = _one_line(value, 200)
    return out


def _normalize_item(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    sid = str(raw.get("id") or "")
    if not valid_space_id(sid):
        return None
    kind = STANDARD_ID if sid == STANDARD_ID else str(raw.get("type") or "agent")
    if kind not in TYPES:
        kind = "agent"
    item: Dict[str, Any] = {
        "id": sid,
        "type": kind,
        "name": _one_line(raw.get("name"), 60),
        "created": int(raw.get("created") or 0) if str(raw.get("created") or "0").isdigit() else 0,
    }
    if kind == "agent":
        item["companion"] = normalize_companion(raw.get("companion") or {})
        item["context"] = normalize_context(raw.get("context"))
        item["global_chat_id"] = _one_line(raw.get("global_chat_id"), 80) or f"g-{sid}"
        item["onboarded"] = bool(raw.get("onboarded"))
    if raw.get("icon"):
        item["icon"] = _one_line(raw.get("icon"), 32)
    return item


def _normalize_state(raw: Any) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    seen = set()
    src = raw.get("items") if isinstance(raw, dict) and isinstance(raw.get("items"), list) else []
    for entry in src[: MAX_SPACES + 4]:
        item = _normalize_item(entry)
        if item and item["id"] not in seen:
            seen.add(item["id"])
            items.append(item)
    if STANDARD_ID not in seen:
        items.insert(0, {"id": STANDARD_ID, "type": STANDARD_ID, "name": "", "created": 0})
    items = items[:MAX_SPACES]
    active = str(raw.get("active_id") or STANDARD_ID) if isinstance(raw, dict) else STANDARD_ID
    if not any(item["id"] == active for item in items):
        active = STANDARD_ID
    return {"version": 1, "active_id": active, "items": items}


def load_state(profile_id: Optional[str] = None) -> Dict[str, Any]:
    # F24: prune the deleted-spaces trash once per process; a failure here
    # must never break loading the workspace registry.
    pid = str(profile_id or active_profile())
    if pid not in _TRASH_PRUNED:
        _TRASH_PRUNED.add(pid)
        try:
            prune_space_trash(pid)
        except Exception:
            pass
    path = _state_path(profile_id)
    key = str(path)
    try:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = None
    cached = _CACHE.get(key)
    if cached and cached[0] == stamp:
        return json.loads(cached[1])
    raw: Any = {}
    if stamp is not None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
    state = _normalize_state(raw)
    _CACHE[key] = (stamp, json.dumps(state, ensure_ascii=False))
    return state


def _save_state(state: Dict[str, Any], profile_id: Optional[str] = None) -> Dict[str, Any]:
    clean = _normalize_state(state)
    path = _state_path(profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    from .storage import save_json
    save_json(str(path), clean, backup=False)
    _CACHE.pop(str(path), None)
    return clean


def active_space_id(profile_id: Optional[str] = None) -> str:
    try:
        return load_state(profile_id)["active_id"]
    except Exception:
        return STANDARD_ID


@contextmanager
def workspace_scope(profile_id: str, space_id: str):
    """Keep a background job's storage in the workspace that started it."""
    token = _WORKSPACE_SCOPE.set((profile_id, space_id))
    try:
        yield
    finally:
        _WORKSPACE_SCOPE.reset(token)


def _workspace_of(profile_id: Optional[str]) -> tuple[str, str]:
    scope = _WORKSPACE_SCOPE.get()
    pid = str(profile_id or (scope[0] if scope else active_profile()))
    if scope and scope[0] == pid:
        if get_space(scope[1], pid) is None:
            raise ValueError("Рабочая область удалена")
        return scope
    return pid, active_space_id(pid)


def get_space(space_id: str, profile_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return next((item for item in load_state(profile_id)["items"] if item["id"] == space_id), None)


def active_space(profile_id: Optional[str] = None) -> Dict[str, Any]:
    state = load_state(profile_id)
    return next(item for item in state["items"] if item["id"] == state["active_id"])


def space_rel_dir(space_id: str, profile_id: Optional[str] = None) -> Path:
    base = profile_root(profile_id)
    if space_id == STANDARD_ID:
        return base
    if not _SPACE_ID_RE.fullmatch(space_id or ""):
        raise ValueError("bad space id")
    return base / "spaces" / space_id


def space_dir(space_id: Optional[str] = None, profile_id: Optional[str] = None) -> Path:
    """Absolute data directory of a space (the active one by default)."""
    sid = space_id or active_space_id(profile_id)
    return _memu_dir() / space_rel_dir(sid, profile_id)


def scoped_name(name: str, profile_id: Optional[str] = None) -> str:
    """``_profile_data_name`` hook: space-scoped files of a non-standard active space."""
    base = profile_root(profile_id)
    if name in SPACE_FILES:
        _, sid = _workspace_of(profile_id)
        if sid != STANDARD_ID:
            rel = base / "spaces" / sid / name
            if name in SEED_FROM_STANDARD:
                _seed_from_standard(rel, base / name)
            return str(rel)
    return str(base / name) if str(base) not in ("", ".") else name


def _seed_from_standard(rel: Path, standard_rel: Path) -> None:
    root = _memu_dir()
    target, source = root / rel, root / standard_rel
    if target.exists() or not source.is_file():
        return
    with _LOCK:
        if target.exists():
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".seed")
            shutil.copyfile(source, tmp)
            tmp.replace(target)
        except OSError:
            pass


# ── Per-workspace settings ──────────────────────────────────────────────────

def preferences_overlay_path(profile_id: Optional[str] = None, space_id: Optional[str] = None) -> Optional[Path]:
    """Own settings file of a non-standard workspace (the active one by default); ``None``
    for the standard workspace, whose settings are the shared ``preferences.json``."""
    try:
        if space_id:
            pid, sid = str(profile_id or active_profile()), space_id
        else:
            pid, sid = _workspace_of(profile_id)
        if sid == STANDARD_ID:
            return None
        return _memu_dir() / space_rel_dir(sid, pid) / SPACE_PREFS_FILE
    except Exception:
        return None


def split_preferences(prefs: Dict[str, Any]) -> tuple:
    """(own, shared) halves of a preferences dict."""
    own = {k: v for k, v in prefs.items() if k in SPACE_PREF_KEYS}
    shared = {k: v for k, v in prefs.items() if k not in SPACE_PREF_KEYS}
    return own, shared


def read_overlay(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if k in SPACE_PREF_KEYS} if isinstance(data, dict) else {}


def overlay_preferences(prefs: Dict[str, Any], profile_id: Optional[str] = None) -> Dict[str, Any]:
    """Raw ``preferences.json`` with the active workspace's own values on top — for the
    readers outside ``pgdata`` (MemuCode CLI, ``memu``)."""
    own = read_overlay(preferences_overlay_path(profile_id))
    if not own:
        return prefs
    out = dict(prefs if isinstance(prefs, dict) else {})
    out.update(own)
    return out


def space_meta(profile_id: Optional[str] = None) -> Dict[str, Any]:
    """What the settings window shows about whose settings it edits."""
    try:
        item = active_space(profile_id)
    except Exception:
        item = {"id": STANDARD_ID, "type": "standard", "name": ""}
    return {"id": item.get("id") or STANDARD_ID, "type": item.get("type") or "standard",
            "name": item.get("name") or "", "own_keys": sorted(SPACE_PREF_KEYS)}


def copy_settings(target_id: str, source_id: str, profile_id: Optional[str] = None) -> Dict[str, Any]:
    """Give ``target_id`` the own settings (and panels) of ``source_id``."""
    from . import pgdata
    from .storage import mutate_json
    pid = str(profile_id or active_profile())
    state = load_state(pid)
    ids = {item["id"] for item in state["items"]}
    if target_id not in ids or source_id not in ids:
        raise KeyError(target_id if target_id not in ids else source_id)
    if target_id == source_id:
        raise ValueError("Выберите другую рабочую область")
    base = pgdata._normalize_preferences(pgdata._load_base_preferences())
    src_path = preferences_overlay_path(pid, source_id)
    source = dict(split_preferences(base)[0])
    if src_path is not None:
        source.update(read_overlay(src_path) or {})
    dst_path = preferences_overlay_path(pid, target_id)
    if dst_path is None:
        mutate_json(pgdata.MEMU_DIR / "preferences.json", {}, lambda raw: (True, pgdata._normalize_preferences({**(raw if isinstance(raw, dict) else {}), **source})),
                    backup=False, validator=lambda value: isinstance(value, dict), repair_invalid=True)
    else:
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        mutate_json(dst_path, {}, lambda raw: (True, dict(source)), backup=False,
                    validator=lambda value: isinstance(value, dict), repair_invalid=True)
    root = _memu_dir()
    src_panels = root / space_rel_dir(source_id, pid) / "panels.json"
    dst_panels = root / space_rel_dir(target_id, pid) / "panels.json"
    if src_panels.is_file():
        try:
            dst_panels.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst_panels.with_name(dst_panels.name + ".seed")
            shutil.copyfile(src_panels, tmp)
            tmp.replace(dst_panels)
        except OSError:
            pass
    return public_state(pid)


def memory_owner(profile_id: Optional[str] = None, space_id: Optional[str] = None) -> str:
    if space_id:
        pid, sid = str(profile_id or active_profile()), space_id
    else:
        pid, sid = _workspace_of(profile_id)
    if sid == STANDARD_ID:
        return pid
    owner = f"{pid}--{sid}"
    return owner if _PROFILE_RE.fullmatch(owner) else f"{pid[:56]}--{sid}"


def owner_key(profile_or_owner: str) -> str:
    """A memory owner key for ``memory_v2``: plain profile ids follow that profile's active space."""
    value = str(profile_or_owner or "")
    if not value or "--" in value:
        return value
    try:
        return memory_owner(value)
    except Exception:
        return value


def public_state(profile_id: Optional[str] = None) -> Dict[str, Any]:
    state = load_state(profile_id)
    out = dict(state)
    out["active"] = next(item for item in state["items"] if item["id"] == state["active_id"])
    out["limits"] = {"max_spaces": MAX_SPACES, "styles": list(STYLES), "economy": list(ECONOMY)}
    return out


def _new_id(used: set) -> str:
    while True:
        sid = "s-" + secrets.token_hex(4)
        if sid not in used:
            return sid


def create_space(body: Dict[str, Any], profile_id: Optional[str] = None) -> Dict[str, Any]:
    kind = str(body.get("type") or "agent")
    if kind not in TYPES:
        raise ValueError("Неизвестный тип рабочей области")
    with _LOCK:
        state = load_state(profile_id)
        if len(state["items"]) >= MAX_SPACES:
            raise ValueError(f"Не больше {MAX_SPACES} рабочих областей")
        sid = _new_id({item["id"] for item in state["items"]})
        item: Dict[str, Any] = {"id": sid, "type": kind, "name": _one_line(body.get("name"), 60),
                                "created": _now_ms()}
        if kind == "agent":
            item["companion"] = normalize_companion(body.get("companion") or {})
            item["context"] = normalize_context(body.get("context"))
            item["global_chat_id"] = f"g-{sid}"
        if body.get("icon"):
            item["icon"] = _one_line(body.get("icon"), 32)
        current = state["active_id"]
        state["items"].append(item)
        folder = _memu_dir() / space_rel_dir(sid, profile_id)
        existed_before = folder.exists()
        folder.mkdir(parents=True, exist_ok=True)
        try:
            _start_settings(folder, current, profile_id)
            _save_state(state, profile_id)
        except Exception:
            # F24: never leave an orphan folder without a registry entry.
            if not existed_before:
                shutil.rmtree(folder, ignore_errors=True)
            raise
        if body.get("activate"):
            activate_space(sid, profile_id)
        return get_space(sid, profile_id) or item


def _start_settings(folder: Path, current_id: str, profile_id: Optional[str] = None) -> None:
    """A new workspace starts as a copy of the current one: its own settings and panels."""
    try:
        from . import pgdata
        from .storage import save_json
        own = split_preferences(pgdata.load_preferences())[0]
        # F24: atomic write (tmp+replace+fsync) — a crash mid-write used to
        # leave a torn preferences.json behind (see also F17).
        save_json(str(folder / SPACE_PREFS_FILE), own, backup=False)
    except Exception:
        pass
    try:
        panels = _memu_dir() / space_rel_dir(current_id, profile_id) / "panels.json"
        target = folder / "panels.json"
        if panels.is_file() and not target.exists():
            # F24: tmp+replace, like _seed_from_standard — never copy
            # directly onto the live file.
            tmp = target.with_name(target.name + ".seed")
            shutil.copyfile(panels, tmp)
            tmp.replace(target)
    except (OSError, ValueError):
        pass


def update_space(space_id: str, patch: Dict[str, Any], profile_id: Optional[str] = None) -> Dict[str, Any]:
    with _LOCK:
        state = load_state(profile_id)
        item = next((row for row in state["items"] if row["id"] == space_id), None)
        if item is None:
            raise KeyError(space_id)
        if "name" in patch:
            item["name"] = _one_line(patch.get("name"), 60)
        if "icon" in patch:
            item["icon"] = _one_line(patch.get("icon"), 32)
        if item["type"] == "agent":
            if isinstance(patch.get("companion"), dict):
                merged = dict(item.get("companion") or {})
                merged.update(patch["companion"])
                item["companion"] = normalize_companion(merged, item.get("companion") or {}, touch=True)
            if isinstance(patch.get("context"), dict):
                item["context"] = normalize_context(patch["context"], item.get("context"))
            if "onboarded" in patch:
                item["onboarded"] = bool(patch.get("onboarded"))
        _save_state(state, profile_id)
        return get_space(space_id, profile_id) or item


def activate_space(space_id: str, profile_id: Optional[str] = None) -> Dict[str, Any]:
    pid = str(profile_id or active_profile())
    with _LOCK:
        state = load_state(pid)
        if not any(item["id"] == space_id for item in state["items"]):
            raise KeyError(space_id)
        old_owner = memory_owner(pid, state["active_id"])
        state["active_id"] = space_id
        _save_state(state, pid)
        new_owner = memory_owner(pid, space_id)
    if pid == active_profile() and old_owner != new_owner:
        try:
            from . import memory_v2
            memory_v2.switch_profile(old_owner, new_owner)
        except Exception:
            pass
    return public_state(pid)


def delete_space(space_id: str, profile_id: Optional[str] = None) -> Dict[str, Any]:
    if space_id == STANDARD_ID:
        raise ValueError("Стандартную рабочую область нельзя удалить")
    pid = str(profile_id or active_profile())
    from . import space_context

    with _LOCK:
        state = load_state(pid)
        if not any(item["id"] == space_id for item in state["items"]):
            raise KeyError(space_id)
        was_active = state["active_id"] == space_id
        folder = space_dir(space_id, pid)
        archived = None
        if folder.is_dir():
            space_context.close_thread(folder / "thread.db")
            trash = folder.parent / ".trash"
            trash.mkdir(parents=True, exist_ok=True)
            archived = trash / f"{space_id}-{time.time_ns()}"
            folder.rename(archived)
        state["items"] = [item for item in state["items"] if item["id"] != space_id]
        if was_active:
            state["active_id"] = STANDARD_ID
        try:
            _save_state(state, pid)
        except Exception:
            if archived is not None:
                archived.rename(folder)
            raise
    try:
        from . import memory_v2
        owner = memory_owner(pid, space_id)
        if was_active and pid == active_profile():
            memory_v2.switch_profile(owner, memory_owner(pid, STANDARD_ID))
        memory_v2.delete_profile_memory(owner)
    except Exception:
        pass
    return public_state(pid)


def register(app) -> None:
    """HTTP API of the window server (``/api/spaces``)."""
    from fastapi import Request
    from fastapi.responses import JSONResponse

    def err(message: str, status: int = 400):
        return JSONResponse({"error": message}, status_code=status)

    def _auth(request: Request) -> Optional[JSONResponse]:
        # F22: the same in-file check as /v1/chat/completions. The Playground
        # Host/Origin middleware is not a token auth, so a cross-site website
        # must not be able to create/delete spaces or wipe thread data.
        # Keyless loopback UI calls (no Origin, or same-authority Origin)
        # pass exactly like they do on /v1/chat/completions.
        from .config import config_store
        auth = request.headers.get("authorization", "")
        key = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("x-api-key")
        if not config_store.authorize_api_request(
            key, host=request.headers.get("host", ""),
            origin=request.headers.get("origin", ""),
            fetch_site=request.headers.get("sec-fetch-site", ""),
            peer=request.client.host if request.client else "",
        ):
            return JSONResponse(status_code=401, content={"error": "Неверный API-ключ хаба."})
        return None

    async def body_of(request: Request) -> Dict[str, Any]:
        # Malformed JSON must be a 400, not a silent default: previously any
        # parse error was swallowed and POST /api/spaces created a junk
        # unnamed agent space (verified: `{bad json` -> 200 + new space).
        # An empty body is still treated as {} for callers that POST
        # without one; a non-empty body must be a JSON object.
        try:
            raw = await request.body()
        except Exception:
            return {}
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw)
        except Exception:
            raise ValueError("Некорректный JSON")
        return data if isinstance(data, dict) else {}

    @app.get("/api/spaces")
    async def spaces_list(request: Request):
        denied = _auth(request)
        if denied is not None:
            return denied
        return public_state()

    @app.post("/api/spaces")
    async def spaces_create(request: Request):
        denied = _auth(request)
        if denied is not None:
            return denied
        try:
            item = create_space(await body_of(request))
        except ValueError as exc:
            return err(str(exc))
        return {"space": item, "state": public_state()}

    @app.patch("/api/spaces/{space_id}")
    async def spaces_update(space_id: str, request: Request):
        denied = _auth(request)
        if denied is not None:
            return denied
        if not valid_space_id(space_id):
            return err("bad space id")
        try:
            item = update_space(space_id, await body_of(request))
        except KeyError:
            return err("Рабочая область не найдена", 404)
        except ValueError as exc:
            return err(str(exc))
        return {"space": item, "state": public_state()}

    @app.post("/api/spaces/{space_id}/activate")
    async def spaces_activate(space_id: str, request: Request):
        denied = _auth(request)
        if denied is not None:
            return denied
        if not valid_space_id(space_id):
            return err("bad space id")
        try:
            return activate_space(space_id)
        except KeyError:
            return err("Рабочая область не найдена", 404)

    @app.post("/api/spaces/{space_id}/copy-settings")
    async def spaces_copy_settings(space_id: str, request: Request):
        denied = _auth(request)
        if denied is not None:
            return denied
        try:
            body = await body_of(request)
        except ValueError as exc:
            return err(str(exc))
        source = str(body.get("from") or "")
        if not valid_space_id(space_id) or not valid_space_id(source):
            return err("bad space id")
        try:
            import asyncio
            return await asyncio.to_thread(copy_settings, space_id, source)
        except KeyError:
            return err("Рабочая область не найдена", 404)
        except ValueError as exc:
            return err(str(exc))

    @app.delete("/api/spaces/{space_id}")
    async def spaces_delete(space_id: str, request: Request):
        denied = _auth(request)
        if denied is not None:
            return denied
        if not valid_space_id(space_id):
            return err("bad space id")
        try:
            return delete_space(space_id)
        except KeyError:
            return err("Рабочая область не найдена", 404)
        except ValueError as exc:
            return err(str(exc))
        except OSError:
            return err("Не удалось переместить файлы рабочей области. Закройте приложения, которые их используют, и повторите попытку.", 409)
