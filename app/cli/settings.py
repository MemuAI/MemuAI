"""Layered MemuCode settings.

Precedence, lowest first: built-in defaults, the user file
(``%APPDATA%\\MemuAI\\memucode\\settings.json``), the shared project file
(``.memucode/settings.json``), the personal project file
(``.memucode/settings.local.json``) and finally command-line overrides.

Dictionaries merge key by key. Permission rule lists and hooks are
*concatenated* across layers — a project can add rules, but never silently
drop the rules of the user. Every other list is replaced by the higher layer.
Secrets (API keys) are accepted only in the user file: a repository must not
be able to smuggle a key, and a key must never end up in a commit.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..storage import load_json_with_recovery, mutate_json, save_json
from . import paths

PERMISSION_MODES = ("default", "acceptEdits", "plan", "auto", "bypassPermissions")
EFFORTS = ("auto", "off", "on", "minimal", "low", "medium", "high", "xhigh", "max")   # Round 33: + off / on / xhigh
THEMES = ("dark", "light", "dark-daltonized", "light-daltonized", "dark-ansi", "light-ansi")
SHELLS = ("auto", "bash", "powershell", "pwsh", "cmd")
OUTPUT_STYLES = ("default", "explanatory", "learning", "concise")
NOTIFY = ("auto", "bell", "toast", "off")

DEFAULTS: Dict[str, Any] = {
    # --- models ---
    "model": "",
    "small_model": "",
    "worker_model": "",
    "worker_models": [],           # models agents may use; with several, one is chosen per agent
    "worker_pick": "model",        # model = the main model chooses | auto = decision model and remarks | first
    "advisor_model": "",
    "vision_model": "",            # legacy: the image model is MemuAI's vision_fallback_model (see vision.py)
    "vision_mode": "auto",         # auto = a picture the model cannot see is described by the image model | off
    "browser_model": "",
    "computer_model": "",
    "model_routing": {"enabled": False, "tiers": {"fast": [], "balanced": [], "strong": []}},
    "model_notes": {},             # {model id: what the user says it is good at}
    "effort": "auto",
    "thinking": False,
    "web_search": False,
    "temperature": None,
    "max_output_tokens": 0,
    # --- connection ---
    "backend": "auto",
    "proxy_url": "",
    "proxy_api_key": "",
    "proxy_autostart": True,
    "connections": [],
    "tool_protocol": "auto",
    # --- behaviour ---
    "permission_mode": "default",
    "permission_sync": True,       # a mode chosen in one window applies to every MemuCode window
    "permissions": {"allow": [], "deny": [], "ask": [], "additional_directories": []},
    "max_turns": 80,
    "subagents": "ask",            # ask = only when the user asks for agents | auto = the model decides | off
    "auto_compact": True,
    "auto_compact_threshold": 85,
    # Round 32: tokens of history past which old large tool results are stubbed (0 = only by window share)
    "context_budget": 40000,
    "context_window": 0,
    "keep_recent_messages": 8,
    "output_style": "default",
    "language": "en",
    "memory_files": ["MEMUCODE.md", "AGENTS.md", "CLAUDE.md"],
    "memuai_memory": True,         # Round 29: facts from MemuAI memory (shared with the MemuAI window)
    "respect_gitignore": True,
    "auto_title": True,
    "include_co_authored_by": True,
    "cleanup_period_days": 45,
    "env": {},
    "hooks": {},
    # --- tools ---
    "shell": "auto",
    "sandbox": {"enabled": True, "env": "filtered", "memory_mb": 2048},
    "browser": {"enabled": True},
    "computer": {"enabled": True, "ask_each_action": True},
    "web": {"fetch_ask": True},
    # --- interface ---
    "theme": "dark",
    "accent": "",
    "vim_mode": False,
    "show_thinking": False,
    "verbose": False,
    "spinner_tips": True,
    "todo_panel": True,
    "notifications": "auto",
    "notify_after_seconds": 25,
    "editor": "",
    "statusline": {"type": "builtin", "command": ""},
    "footer_metric": "cost",
    "tips": True,
    # --- services ---
    "decisions": {
        "provider": "off",           # off | typesafe | openrouter | custom | model
        "api_key": "",
        "base_url": "",
        "model": "",
        "use_models": {},            # a model of its own per kind of check: {"auto_mode": "…", "routing": "…"}
        "fallbacks": [],             # models tried in turn when the chosen one fails
        "uses": {"auto_mode": True, "routing": True, "agents": True, "skills": True, "goal": True, "intent": True},
    },
    "skills": {"disabled": []},    # skill ids switched off in /skills
    # Round 33: /auto-continue — retries while still limited, a model for those retries, the text it sends
    "auto_continue": {"retries": 4, "fallback_model": "", "message": ""},
    "pricing": {"overrides": {}, "online_catalog": True, "show_in_footer": True},
    "stats": {"record": True},
    # --- user-only state ---
    "trusted_dirs": [],
    "onboarded": False,
}

#: Keys that may contain secrets. Only the user layer may define them.
SECRET_PATHS = ("proxy_api_key", "connections", "decisions.api_key")
#: Lists that accumulate across layers instead of being replaced.
CONCAT_PATHS = ("permissions.allow", "permissions.deny", "permissions.ask",
                "permissions.additional_directories")
#: Keys only the user file may set (machine-wide state).
USER_ONLY = ("trusted_dirs", "onboarded", "model_notes", "model_routing", "worker_models", "worker_pick")

SCOPES = ("user", "project", "local")


def _get_path(data: Dict[str, Any], dotted: str, default: Any = None) -> Any:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _set_path(data: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = node[part] = {}
        node = child
    node[parts[-1]] = value


def _del_path(data: Dict[str, Any], dotted: str) -> bool:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        node = node.get(part) if isinstance(node, dict) else None
        if not isinstance(node, dict):
            return False
    if isinstance(node, dict) and parts[-1] in node:
        del node[parts[-1]]
        return True
    return False


def _strip(data: Dict[str, Any], dotted_keys: Iterable[str]) -> Dict[str, Any]:
    clean = copy.deepcopy(data)
    for key in dotted_keys:
        _del_path(clean, key)
    return clean


def _merge(base: Dict[str, Any], layer: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    for key, value in layer.items():
        dotted = f"{prefix}{key}"
        if dotted in CONCAT_PATHS and isinstance(value, list):
            current = base.get(key) if isinstance(base.get(key), list) else []
            base[key] = list(dict.fromkeys([*current, *[str(item) for item in value]]))
        elif dotted == "hooks" and isinstance(value, dict):
            hooks = base.setdefault("hooks", {})
            for event, rows in value.items():
                if isinstance(rows, list):
                    hooks[event] = [*hooks.get(event, []), *rows]
        elif isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _merge(dict(base[key]), value, dotted + ".")
        else:
            base[key] = copy.deepcopy(value)
    return base


def _read(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        # A hand-edited file with a syntax error must not wipe the settings:
        # the user file has rotating backups, a project file is left alone.
        data = load_json_with_recovery(path, {}, validator=lambda x: isinstance(x, dict))
    return data if isinstance(data, dict) else {}


class SettingsError(ValueError):
    pass


class Settings:
    """Merged view over all layers. Cheap to reload; writes go to one layer."""

    def __init__(self, root: Path | str, overrides: Optional[Dict[str, Any]] = None) -> None:
        self.root = Path(root).resolve()
        self.overrides: Dict[str, Any] = copy.deepcopy(overrides or {})
        self._lock = threading.RLock()
        self.layers: List[Tuple[str, Path, Dict[str, Any]]] = []
        self.data: Dict[str, Any] = {}
        self.warnings: List[str] = []
        self.reload()

    # ------------------------------------------------------------ files
    def file_for(self, scope: str) -> Path:
        if scope == "user":
            return paths.user_settings_file()
        if scope == "project":
            return paths.project_dir(self.root) / "settings.json"
        if scope == "local":
            return paths.project_dir(self.root) / "settings.local.json"
        raise SettingsError(f"unknown scope {scope}")

    def reload(self) -> None:
        with self._lock:
            self.warnings = []
            user = _read(self.file_for("user"))
            project = _read(self.file_for("project"))
            local = _read(self.file_for("local"))
            for name, layer in (("project", project), ("local", local)):
                leaked = [key for key in SECRET_PATHS if _get_path(layer, key) not in (None, "", [], {})]
                if leaked:
                    self.warnings.append(f"{name}: ignored secret keys {', '.join(leaked)}")
            project = _strip(project, (*SECRET_PATHS, *USER_ONLY))
            local = _strip(local, (*SECRET_PATHS, *USER_ONLY))
            self.layers = [("default", Path(), DEFAULTS), ("user", self.file_for("user"), user),
                           ("project", self.file_for("project"), project),
                           ("local", self.file_for("local"), local),
                           ("cli", Path(), self.overrides)]
            # Trust comes only from the user/command line, never from files in
            # the repository being opened. Keep raw layers available for edits.
            self.data = _merge(_merge(copy.deepcopy(DEFAULTS), user), self.overrides)
            trusted = self.is_trusted()
            merged: Dict[str, Any] = copy.deepcopy(DEFAULTS)
            for name, _path, layer in self.layers[1:]:
                if name in ("project", "local") and not trusted:
                    if layer:
                        self.warnings.append(f"{name}: settings ignored until the project is trusted")
                    continue
                merged = _merge(merged, layer)
            self.data = merged

    # ------------------------------------------------------------ reads
    def get(self, key: str, default: Any = None) -> Any:
        value = _get_path(self.data, key, None)
        if value is None:
            fallback = _get_path(DEFAULTS, key, None)
            return default if fallback is None else copy.deepcopy(fallback)
        return value

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def source_of(self, key: str) -> str:
        for name, _path, layer in reversed(self.layers):
            if name in ("project", "local") and not self.is_trusted():
                continue
            if _get_path(layer, key, None) is not None:
                return name
        return "default"

    def layer(self, scope: str) -> Dict[str, Any]:
        for name, _path, data in self.layers:
            if name == scope:
                return data
        return {}

    # ------------------------------------------------------------ writes
    def set(self, key: str, value: Any, scope: str = "user") -> None:
        if scope not in SCOPES:
            raise SettingsError(f"unknown scope {scope}")
        root_key = key.split(".")[0]
        if scope != "user" and (key in SECRET_PATHS or root_key in USER_ONLY
                                or any(key.startswith(secret + ".") or secret.startswith(key + ".")
                                       for secret in SECRET_PATHS)):
            raise SettingsError("secrets and machine-wide state live only in the user settings")
        value = validate(key, value)
        path = self.file_for(scope)

        def change(data: Any):
            data = data if isinstance(data, dict) else {}
            _set_path(data, key, value)
            return True, data

        with self._lock:
            if scope == "user":
                mutate_json(path, {}, change, backup=True, validator=lambda x: isinstance(x, dict))
            else:
                data = _read(path)
                _set_path(data, key, value)
                path.parent.mkdir(parents=True, exist_ok=True)
                save_json(path, data, backup=False)
                if scope == "local":
                    _ensure_gitignored(self.root)
            self.reload()

    def unset(self, key: str, scope: str = "user") -> bool:
        path = self.file_for(scope)
        removed = {"value": False}

        def change(data: Any):
            data = data if isinstance(data, dict) else {}
            removed["value"] = _del_path(data, key)
            return removed["value"], data

        with self._lock:
            if scope == "user":
                mutate_json(path, {}, change, backup=True, validator=lambda x: isinstance(x, dict))
            else:
                data = _read(path)
                if _del_path(data, key):
                    save_json(path, data, backup=False)
                    removed["value"] = True
            self.reload()
        return removed["value"]

    def append(self, key: str, item: Any, scope: str = "local") -> None:
        """Add one entry to a list setting (permission rules, directories)."""
        current = _get_path(self.layer(scope), key, None)
        values = list(current) if isinstance(current, list) else []
        if item not in values:
            values.append(item)
        self.set(key, values, scope)

    def remove_item(self, key: str, item: Any, scope: str) -> bool:
        current = _get_path(self.layer(scope), key, None)
        if not isinstance(current, list) or item not in current:
            return False
        self.set(key, [value for value in current if value != item], scope)
        return True

    # ------------------------------------------------------------ trust
    def is_trusted(self, folder: Path | str | None = None) -> bool:
        target = os.path.normcase(str(Path(folder or self.root).resolve()))
        for entry in self.get("trusted_dirs") or []:
            base = os.path.normcase(str(entry))
            if target == base or target.startswith(base.rstrip("\\/") + os.sep):
                return True
        return False

    def trust(self, folder: Path | str | None = None) -> None:
        self.append("trusted_dirs", str(Path(folder or self.root).resolve()), scope="user")

    # ------------------------------------------------------------ connections
    def connections(self) -> List[Dict[str, Any]]:
        """Direct API connections: configured ones plus well-known env keys."""
        rows = [dict(row) for row in (self.get("connections") or []) if isinstance(row, dict) and row.get("name")]
        names = {str(row["name"]).lower() for row in rows}
        env_defaults = (
            ("anthropic", "ANTHROPIC_API_KEY", "anthropic", "https://api.anthropic.com"),
            ("openai", "OPENAI_API_KEY", "openai", "https://api.openai.com/v1"),
            ("openrouter", "OPENROUTER_API_KEY", "openai", "https://openrouter.ai/api/v1"),
        )
        for name, variable, fmt, base in env_defaults:
            key = os.environ.get(variable, "").strip()
            if key and name not in names:
                rows.append({"name": name, "format": fmt, "base_url": base, "api_key": key,
                             "models": [], "from_env": variable})
        return rows


def validate(key: str, value: Any) -> Any:
    """Coerce a value typed by a human (``/config set``) to the setting's type."""
    default = _get_path(DEFAULTS, key, None)
    choices = {
        "permission_mode": PERMISSION_MODES, "effort": EFFORTS, "theme": THEMES,
        "shell": SHELLS, "notifications": NOTIFY, "subagents": ("ask", "auto", "off"),
        "backend": ("auto", "proxy", "direct"), "tool_protocol": ("auto", "text", "native"),
        "language": ("", "ru", "en"), "sandbox.env": ("filtered", "strict", "full"),
        "statusline.type": ("builtin", "command", "off"),
        "decisions.provider": ("off", "typesafe", "openrouter", "custom", "model"),
    }.get(key)
    if isinstance(value, str) and isinstance(default, bool):
        low = value.strip().lower()
        if low in ("1", "true", "yes", "on", "да", "вкл"):
            return True
        if low in ("0", "false", "no", "off", "нет", "выкл"):
            return False
        raise SettingsError(f"{key}: expected true/false")
    if isinstance(default, bool) and not isinstance(value, bool):
        raise SettingsError(f"{key}: expected true/false")
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise SettingsError(f"{key}: expected a number") from exc
        limits = {"auto_compact_threshold": (40, 98), "max_turns": (1, 1000),
                  "keep_recent_messages": (2, 60), "context_window": (0, 20_000_000),
                  "context_budget": (0, 20_000_000),
                  "cleanup_period_days": (1, 3650), "notify_after_seconds": (0, 86400),
                  "max_output_tokens": (0, 1_000_000), "sandbox.memory_mb": (256, 65536)}.get(key)
        if limits:
            number = max(limits[0], min(limits[1], number))
        return number
    if isinstance(default, list) and isinstance(value, str):
        value = [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(default, dict) and isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise SettingsError(f"{key}: expected JSON object") from exc
    if choices is not None:
        if value not in choices:
            raise SettingsError(f"{key}: one of {', '.join(repr(c) for c in choices if c) or choices}")
    return value


def _ensure_gitignored(root: Path) -> None:
    """settings.local.json is personal: keep it out of commits."""
    if not (root / ".git").exists():
        return
    ignore = root / ".gitignore"
    entry = ".memucode/settings.local.json"
    try:
        text = ignore.read_text(encoding="utf-8") if ignore.is_file() else ""
        if entry in text.splitlines():
            return
        with ignore.open("a", encoding="utf-8") as handle:
            handle.write(("" if not text or text.endswith("\n") else "\n") + entry + "\n")
    except OSError:
        pass


def flatten(data: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    """``{"a": {"b": 1}}`` → ``{"a.b": 1}`` for listing and completion."""
    rows: Dict[str, Any] = {}
    for key, value in data.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict) and value and dotted not in ("env", "hooks", "pricing.overrides", "model_notes"):
            rows.update(flatten(value, dotted + "."))
        else:
            rows[dotted] = value
    return rows


def mask(key: str, value: Any) -> Any:
    """Secrets are shown as ``sk-…abcd``."""
    low = key.lower()
    if isinstance(value, str) and value and any(word in low for word in ("key", "token", "secret")):
        return (value[:3] + "…" + value[-4:]) if len(value) > 10 else "••••"
    if key == "connections" and isinstance(value, list):
        return [{**row, "api_key": mask("api_key", row.get("api_key", ""))} if isinstance(row, dict) else row
                for row in value]
    return value
