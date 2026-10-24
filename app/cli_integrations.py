"""Install / reset Proxy or direct API models in Claude Code and Codex CLI.

Both integrations configure a *third-party CLI tool* without requiring users to
hand-edit env vars or config files. They can point at the Proxy or a direct API:

* Claude Code reads ``~/.claude/settings.json`` — an ``"env"`` block with
  ``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_AUTH_TOKEN`` / ``ANTHROPIC_MODEL`` and the
  per-alias ``ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU,FABLE}_MODEL`` slots (plus the
  optional ``CLAUDE_CODE_SUBAGENT_MODEL``). It speaks the Anthropic Messages API,
  which :mod:`app.anthropic_api` now exposes at ``/v1/messages``.
* Codex CLI reads ``~/.codex/config.toml`` — a ``[model_providers.<id>]`` table
  pointing at our Responses-compatible ``/v1``, plus a ``[profiles.<name>]`` table per
  model the user wants (so they can run ``codex --profile <name>``), and,
  when it is safe to do so, top-level ``model`` / ``model_provider`` defaults.

Agent tools require an OpenAI-compatible Custom API connection whose upstream
model implements function calling. Other providers remain available for text,
but the API rejects tool-bearing requests explicitly.

Both apply() functions are pure merges: every other key in the user's file is
left untouched, a full-file backup is written once (before our very first
change), and the *prior* value of every key we ever touch is recorded so
reset() can restore it exactly (or delete the key if it did not exist before).
Everything here is plain functions over an explicit ``home`` (and, for the
state file, an explicit ``state_path``) so tests never have to touch the
caller's real ``$HOME``/``%USERPROFILE%``.
"""
from __future__ import annotations

import os
import re
import tomllib
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .config import DATA_DIR, config_store

STATE_PATH = os.path.join(DATA_DIR, "cli_integrations.json")
_LOCK = threading.RLock()

# ---------------------------------------------------------------------------
# home / paths
# ---------------------------------------------------------------------------


def home_dir(home: Optional[str] = None) -> str:
    if home:
        return home
    override = os.environ.get("MEMUAI_HOME_OVERRIDE")
    if override:
        return override
    return os.path.expanduser("~")


def claude_settings_path(home: Optional[str] = None) -> str:
    return os.path.join(home_dir(home), ".claude", "settings.json")


def codex_config_path(home: Optional[str] = None) -> str:
    return os.path.join(home_dir(home), ".codex", "config.toml")


def proxy_base_url() -> str:
    """A literal, connectable loopback URL — never the bind-all 0.0.0.0/::."""
    host = str(config_store.effective_host or "127.0.0.1").strip().strip("[]")
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    port = config_store.effective_port
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}/v1"


def proxy_api_key() -> str:
    keys = config_store.settings.api_keys
    return keys[0] if keys else ""


# ---------------------------------------------------------------------------
# atomic file helpers
# ---------------------------------------------------------------------------


def _atomic_write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(8):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if os.name != "nt" or attempt == 7:
                raise
            time.sleep(min(0.01 * (2 ** attempt), 0.1))


def _ensure_backup(path: str, backup_path: str) -> None:
    """Snapshot the pristine file once, before we ever touch it."""
    if os.path.exists(backup_path):
        return
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        original = f.read()
    _atomic_write_text(backup_path, original)


# ---------------------------------------------------------------------------
# small JSON state store (proxy data dir — NOT the user's $HOME)
# ---------------------------------------------------------------------------


def _load_state(state_path: str) -> Dict[str, Any]:
    import json
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state_path: str, data: Dict[str, Any]) -> None:
    import json
    _atomic_write_text(state_path, json.dumps(data, ensure_ascii=False, indent=2))


class IntegrationError(Exception):
    def __init__(self, message: str, message_en: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.message_en = message_en or message


# ---------------------------------------------------------------------------
# Claude Code — ~/.claude/settings.json  ("env" block)
# ---------------------------------------------------------------------------

CLAUDE_SLOT_ENV = {
    "model": "ANTHROPIC_MODEL",
    "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "fable": "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "subagent": "CLAUDE_CODE_SUBAGENT_MODEL",
}
# Always managed (base connection), plus whichever CLAUDE_SLOT_ENV keys are
# selected this call.
CLAUDE_BASE_ENV = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN")
_PLACEHOLDER_TOKEN = "memuai-local-proxy"


def _read_json_file(path: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Returns (parsed_or_None, existed). Raises IntegrationError on invalid JSON."""
    import json
    if not os.path.exists(path):
        return {}, False
    # utf-8-sig: settings.json saved by Notepad/PowerShell 5.1 starts with a BOM; json.loads
    # rejected it and the user was told their (valid) file had invalid JSON.
    with open(path, "r", encoding="utf-8-sig") as f:
        raw = f.read()
    if not raw.strip():
        return {}, True
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise IntegrationError(
            f"{path} содержит некорректный JSON — файл не тронут ({exc}). Исправьте его вручную и повторите.",
            f"{path} has invalid JSON — the file was left untouched ({exc}). Fix it by hand and try again.",
        )
    if not isinstance(data, dict):
        raise IntegrationError(
            f"{path} должен быть JSON-объектом верхнего уровня — файл не тронут.",
            f"{path} must be a top-level JSON object — the file was left untouched.",
        )
    return data, True


def claude_status(home: Optional[str] = None, state_path: Optional[str] = None) -> Dict[str, Any]:
    state_path = state_path or STATE_PATH
    state = _load_state(state_path).get("claude") or {}
    path = claude_settings_path(home)
    return {
        "installed": bool(state.get("managed")),
        "path": path,
        "backup_path": path + ".memuai-backup",
        "connection": state.get("connection") or "proxy",
        "env": {k: v for k, v in (state.get("applied") or {}).items()
                if k != "ANTHROPIC_AUTH_TOKEN"},
        "api_key_set": bool(state.get("api_key_set", (state.get("applied") or {}).get(
            "ANTHROPIC_AUTH_TOKEN") not in (None, "", _PLACEHOLDER_TOKEN))),
    }


def apply_claude(selection: Dict[str, str], *, home: Optional[str] = None,
                 state_path: Optional[str] = None, base_url: Optional[str] = None,
                 api_key: Optional[str] = None, connection: str = "proxy") -> Dict[str, Any]:
    """selection: any of the CLAUDE_SLOT_ENV keys -> a proxy model id (public id
    from GET /api/admin/models). Omitted/empty slots are not set (and, if a
    previous apply had set them, they are restored to their pre-MemuAI value)."""
    state_path = state_path or STATE_PATH
    path = claude_settings_path(home)
    backup_path = path + ".memuai-backup"
    base_url = base_url if base_url is not None else proxy_base_url()
    configured_token = api_key if api_key is not None else proxy_api_key()
    token = configured_token or _PLACEHOLDER_TOKEN  # Claude Code refuses an empty token.

    desired: Dict[str, str] = {"ANTHROPIC_BASE_URL": base_url, "ANTHROPIC_AUTH_TOKEN": token}
    for slot, value in (selection or {}).items():
        env_key = CLAUDE_SLOT_ENV.get(slot)
        value = str(value or "").strip()
        if env_key and value:
            desired[env_key] = value[:512]

    with _LOCK:
        data, _existed = _read_json_file(path)  # raises IntegrationError on invalid JSON
        _ensure_backup(path, backup_path)

        env = data.get("env")
        if not isinstance(env, dict):
            env = {}
        data["env"] = env

        full_state = _load_state(state_path)
        claude_state = full_state.get("claude") or {"prior": {}, "managed": []}
        prior: Dict[str, Any] = dict(claude_state.get("prior") or {})
        previously_managed = set(claude_state.get("managed") or [])

        touched = set(desired) | previously_managed
        for key in touched:
            if key not in prior:
                prior[key] = {"existed": key in env, "value": env.get(key)}
            if key in desired:
                env[key] = desired[key]
            else:
                # was managed before, not selected any more: restore original
                record = prior.pop(key, None)
                if record is not None and record.get("existed"):
                    env[key] = record.get("value")
                else:
                    env.pop(key, None)

        _atomic_write_text(path, _json_dumps(data))

        full_state["claude"] = {
            "prior": prior, "managed": sorted(desired.keys()),
            "applied": dict(desired), "path": path, "backup_path": backup_path,
            "connection": connection, "api_key_set": bool(configured_token),
        }
        _save_state(state_path, full_state)

    return {"ok": True, "path": path, "backup_path": backup_path,
            "env": {k: v for k, v in desired.items() if k != "ANTHROPIC_AUTH_TOKEN"}}


def reset_claude(*, home: Optional[str] = None, state_path: Optional[str] = None) -> Dict[str, Any]:
    state_path = state_path or STATE_PATH
    path = claude_settings_path(home)
    with _LOCK:
        full_state = _load_state(state_path)
        claude_state = full_state.get("claude") or {}
        managed = claude_state.get("managed") or []
        prior = claude_state.get("prior") or {}
        if not managed:
            return {"ok": True, "already": True, "path": path}

        data, _existed = _read_json_file(path)
        env = data.get("env")
        if not isinstance(env, dict):
            env = {}
        data["env"] = env
        for key in managed:
            record = prior.get(key)
            if record is not None and record.get("existed"):
                env[key] = record.get("value")
            else:
                env.pop(key, None)
        _atomic_write_text(path, _json_dumps(data))

        full_state["claude"] = {"prior": {}, "managed": [], "applied": {}, "path": path,
                                 "backup_path": path + ".memuai-backup", "connection": "proxy",
                                 "api_key_set": False}
        _save_state(state_path, full_state)
    return {"ok": True, "path": path}


def _json_dumps(data: Dict[str, Any]) -> str:
    import json
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


# ---------------------------------------------------------------------------
# Codex CLI — ~/.codex/config.toml
# ---------------------------------------------------------------------------

CODEX_PROVIDER_ID = "memuai"
_BEGIN = "# >>> MemuAI Proxy managed block - edit from MemuAI Proxy UI, not by hand >>>"
_END = "# <<< MemuAI Proxy managed block <<<"
_BLOCK_RE = re.compile(
    re.escape(_BEGIN) + r".*?" + re.escape(_END) + r"\n?\n?", re.DOTALL)
_TABLE_HEADER_RE = re.compile(r"^\s*\[", re.MULTILINE)
_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def _toml_str(value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    escaped = "".join(ch if ch >= " " or ch in "\t" else "" for ch in escaped)
    return f'"{escaped}"'


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", str(value).strip().lower()).strip("-")
    return slug or "model"


def _strip_managed_block(text: str) -> str:
    return _BLOCK_RE.sub("", text)


def codex_status(home: Optional[str] = None, state_path: Optional[str] = None) -> Dict[str, Any]:
    state_path = state_path or STATE_PATH
    state = _load_state(state_path).get("codex") or {}
    path = codex_config_path(home)
    return {
        "installed": bool(state.get("profiles")),
        "path": path,
        "backup_path": path + ".memuai-backup",
        "connection": state.get("connection") or "proxy",
        "base_url": state.get("base_url") or "",
        "api_key_set": bool(state.get("api_key_set")),
        "profiles": state.get("profiles") or [],
        "default_profile": state.get("default_profile") or "",
        "default_set": bool(state.get("default_written")),
    }


def apply_codex(profiles: List[Dict[str, str]], *, default: Optional[str] = None,
                home: Optional[str] = None, state_path: Optional[str] = None,
                base_url: Optional[str] = None, api_key: Optional[str] = None,
                connection: str = "proxy") -> Dict[str, Any]:
    """profiles: [{"name": <slug, optional>, "model": <proxy model id>}, ...].
    default: profile name (after slugifying) to also install as the top-level
    default model/model_provider, when that is safe (see module docstring)."""
    state_path = state_path or STATE_PATH
    path = codex_config_path(home)
    backup_path = path + ".memuai-backup"
    base_url = base_url if base_url is not None else proxy_base_url()
    key = api_key if api_key is not None else proxy_api_key()

    clean_profiles: List[Dict[str, str]] = []
    seen_names = set()
    for row in profiles or []:
        model = str((row or {}).get("model") or "").strip()
        if not model:
            continue
        name = str((row or {}).get("name") or "").strip() or model
        slug = "memuai-" + _slugify(name)
        base_slug, n = slug, 2
        while slug in seen_names:
            slug = f"{base_slug}-{n}"
            n += 1
        seen_names.add(slug)
        clean_profiles.append({"name": slug, "model": model[:512]})
    if not clean_profiles:
        raise IntegrationError("Выберите хотя бы одну модель для Codex", "Pick at least one model for Codex")

    default_slug = ""
    if default:
        wanted = "memuai-" + _slugify(default)
        default_slug = next((p["name"] for p in clean_profiles if p["name"] == wanted), clean_profiles[0]["name"])
    else:
        default_slug = clean_profiles[0]["name"]
    default_model = next(p["model"] for p in clean_profiles if p["name"] == default_slug)

    with _LOCK:
        original = ""
        existed = os.path.exists(path)
        if existed:
            with open(path, "r", encoding="utf-8-sig") as f:   # a BOM is not valid TOML
                original = f.read()
            try:
                tomllib.loads(original)
            except tomllib.TOMLDecodeError as exc:
                raise IntegrationError(
                    f"{path} содержит некорректный TOML — файл не тронут ({exc}). Исправьте его вручную и повторите.",
                    f"{path} has invalid TOML — the file was left untouched ({exc}). Fix it by hand and try again.")
        _ensure_backup(path, backup_path)

        remainder = _strip_managed_block(original)
        try:
            remainder_doc = tomllib.loads(remainder) if remainder.strip() else {}
        except tomllib.TOMLDecodeError as exc:
            raise IntegrationError(
                f"Не удалось разобрать {path} после удаления старого управляемого блока ({exc}).",
                f"Could not parse {path} after removing the old managed block ({exc}).")

        existing_providers = remainder_doc.get("model_providers")
        if isinstance(existing_providers, dict) and CODEX_PROVIDER_ID in existing_providers:
            raise IntegrationError(
                f"В {path} уже есть таблица [model_providers.{CODEX_PROVIDER_ID}], не созданная MemuAI. "
                "Переименуйте или удалите её вручную и повторите.",
                f"{path} already has a [model_providers.{CODEX_PROVIDER_ID}] table MemuAI did not create. "
                "Rename or remove it by hand and try again.")
        existing_profiles = remainder_doc.get("profiles")
        clashing = [p["name"] for p in clean_profiles
                    if isinstance(existing_profiles, dict) and p["name"] in existing_profiles]
        if clashing:
            raise IntegrationError(
                f"В {path} уже есть профиль(и) {', '.join(clashing)}, не созданные MemuAI.",
                f"{path} already has profile(s) {', '.join(clashing)} MemuAI did not create.")

        can_set_default = "model" not in remainder_doc and "model_provider" not in remainder_doc

        # TOML: a bare `key = value` belongs to whichever table header most
        # recently preceded it — so root-level model/model_provider MUST come
        # before the first `[table]` header in this block, not after it.
        lines = [_BEGIN]
        if can_set_default:
            lines.append(f"model_provider = {_toml_str(CODEX_PROVIDER_ID)}")
            lines.append(f"model = {_toml_str(default_model)}")
            lines.append("")
        lines += [f"[model_providers.{CODEX_PROVIDER_ID}]",
                  f"name = {_toml_str('Direct API' if connection == 'direct' else 'MemuAI Proxy')}",
                  f"base_url = {_toml_str(base_url)}",
                  'wire_api = "responses"']
        if key:
            # The installer cannot inject an environment variable into future
            # Codex processes. Store the same proxy token here as the Claude
            # integration stores in settings.json, inside our resettable block.
            lines.append(f"experimental_bearer_token = {_toml_str(key)}")
        lines.append("")
        for row in clean_profiles:
            lines.append(f'[profiles."{row["name"]}"]')
            lines.append(f"model = {_toml_str(row['model'])}")
            lines.append(f"model_provider = {_toml_str(CODEX_PROVIDER_ID)}")
            lines.append("")
        lines.append(_END)
        block = "\n".join(lines) + "\n"

        match = _TABLE_HEADER_RE.search(remainder)
        if match:
            insert_at = match.start()
            new_text = remainder[:insert_at] + block + "\n" + remainder[insert_at:]
        else:
            sep = "\n" if remainder and not remainder.endswith("\n") else ""
            new_text = (remainder + sep + ("\n" if remainder.strip() else "") + block) if remainder.strip() else block

        try:
            tomllib.loads(new_text)  # safety net: never write TOML we can't parse back
        except tomllib.TOMLDecodeError as exc:
            raise IntegrationError(
                f"Внутренняя ошибка: получившийся {os.path.basename(path)} не проходит проверку TOML ({exc}). Файл не изменён.",
                f"Internal error: the resulting {os.path.basename(path)} fails TOML validation ({exc}). File left unchanged.")

        _atomic_write_text(path, new_text)

        full_state = _load_state(state_path)
        full_state["codex"] = {
            "profiles": clean_profiles, "default_profile": default_slug if can_set_default else "",
            "default_written": can_set_default, "path": path, "backup_path": backup_path,
            "connection": connection, "base_url": base_url, "api_key_set": bool(key),
        }
        _save_state(state_path, full_state)

    return {"ok": True, "path": path, "backup_path": backup_path, "profiles": clean_profiles,
            "default_profile": default_slug if can_set_default else "",
            "default_set": can_set_default,
            "note_en": ("" if can_set_default else
                        "config.toml already defines a top-level model/model_provider; "
                        "MemuAI only added profiles — switch to one with `codex --profile <name>`."),
            "note": ("" if can_set_default else
                     "В config.toml уже есть свои model/model_provider; MemuAI только добавил профили — "
                     "переключайтесь через `codex --profile <name>`.")}


def reset_codex(*, home: Optional[str] = None, state_path: Optional[str] = None) -> Dict[str, Any]:
    state_path = state_path or STATE_PATH
    path = codex_config_path(home)
    with _LOCK:
        full_state = _load_state(state_path)
        codex_state = full_state.get("codex") or {}
        if not codex_state.get("profiles"):
            return {"ok": True, "already": True, "path": path}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8-sig") as f:   # a BOM is not valid TOML
                original = f.read()
            new_text = _strip_managed_block(original)
            try:
                tomllib.loads(new_text)
            except tomllib.TOMLDecodeError as exc:
                raise IntegrationError(
                    f"Не удалось безопасно снять блок MemuAI из {path} ({exc}). Восстановите файл из "
                    f"{path}.memuai-backup вручную.",
                    f"Could not safely remove the MemuAI block from {path} ({exc}). Restore it by hand "
                    f"from {path}.memuai-backup.")
            _atomic_write_text(path, new_text)
        full_state["codex"] = {"profiles": [], "default_profile": "", "default_written": False,
                                "path": path, "backup_path": path + ".memuai-backup",
                                "connection": "proxy", "base_url": "", "api_key_set": False}
        _save_state(state_path, full_state)
    return {"ok": True, "path": path}
