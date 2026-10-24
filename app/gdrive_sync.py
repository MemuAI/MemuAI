"""Round 29 «Синхронизация» (Sync): automatic backups of all MemuAI data, with restore.

What is backed up
-----------------
Everything under the data directory (``%APPDATA%\\MemuAI``) that is the user's own
data, sorted into categories the user can switch on and off (:data:`CATEGORIES`):
settings, chats, memory, prompts, schedules, Work list, modules, panels, Quick Ask,
characters, companion/voice clips, Telegram, Proxy settings, other files.  Never
included: the program, logs, ring backups, caches, browser profiles, downloaded
models (voice, wake word, local LLMs), runtime discovery files and this feature's
own folder ``memuai/sync/``.  Proxy secrets (``accounts.json`` with provider
cookies/tokens, public API keys, MCP env, integration tokens, the DFY vault) form the
``secrets`` category: off by default and only allowed together with encryption.
Secret-looking keys inside ordinary settings files (``telegram_bot_token``,
``smtp_password``, ``*_api_key`` …) are cut out of the copy instead; a restore keeps
the local values of those keys.

Repository format (identical in Google Drive and in a local folder)
-------------------------------------------------------------------
``memuai-manifest-<device>-<snapshot>.json`` — one snapshot.  ``header`` is always
plain (date, device, reason, sizes, categories, referenced packs) so the restore
list works without a passphrase; ``body`` (path → sha256/size/category/pack) is
plain or, with encryption, ``body_enc``.

``memuai-pack-<device>-<id>.zip[.enc]`` — a zip of content-addressed blobs (member
name = sha256).  A snapshot uploads only blobs that no earlier kept snapshot of
this device already stored (incremental, bandwidth-friendly); the manifest is
uploaded last, so an interrupted run never produces a half snapshot.  Retention
deletes old manifests (pinned ones never), then packs no kept manifest references.

``memuai-keys.json`` — public scrypt parameters + a check value per passphrase;
``memuai-pins.json`` — pinned snapshot ids.

Restore is conflict-safe: before writing anything the current versions of every
file it will touch are zipped into ``memuai/sync/safety/`` («undo the restore»).

Scheduling runs in a thread in MemuAI *and* in MemuAI Proxy (which stays in the
tray when the MemuAI window is closed); a cross-process lock file and the shared
state file make sure only one of them works at a time.  On exit MemuAI hands the
backup to a running Proxy, or runs it itself with a time limit.
"""
from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import socket
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from . import gdrive_sync_crypto as crypto

log = logging.getLogger("memuai.sync")

FORMAT = "memuai-backup"
FORMAT_VERSION = 1
EXPORT_FORMAT = "memuai-backup-export"
PACK_LIMIT = 192 * 1024 * 1024
SAFETY_KEEP = 5
HISTORY_KEEP = 25
COMPRESSED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".zip", ".gz", ".7z", ".mp3", ".mp4", ".ogg", ".opus",
                  ".m4a", ".webm", ".wav", ".flac", ".vrm", ".glb", ".pdf", ".docx", ".xlsx", ".pptx", ".woff2", ".moc3"}

# id, ru, en, description ru, description en, default
CATEGORIES: Tuple[Tuple[str, str, str, str, str, bool], ...] = (
    ("settings", "Настройки", "Settings",
     "Параметры MemuAI, профили, свои темы, значок, слово-активатор", "MemuAI preferences, profiles, custom themes, icon, wake word", True),
    ("chats", "Чаты", "Chats", "Все чаты: обычные, Work, быстрого доступа — с созданными картинками и видео",
     "All chats: regular, Work, Quick Ask — with generated images and videos", True),
    ("memory", "Память", "Memory", "Записи памяти и «Рефлексия»", "Memory entries and Reflect", True),
    ("prompts", "Промпты и навыки", "Prompts & skills", "Наборы промптов и навыки MemuCode", "Prompt sets and MemuCode skills", True),
    ("schedules", "Расписания", "Schedules", "Задачи по расписанию и их результаты", "Scheduled tasks and their results", True),
    ("work", "Work и MemuCode", "Work & MemuCode", "Список Work-проектов и настройки MemuCode (файлы самих проектов не трогаются)",
     "The Work project list and MemuCode settings (project files themselves are not touched)", True),
    ("modules", "Модули", "Modules", "Установленные модули и их данные", "Installed modules and their data", True),
    ("panels", "Панели", "Panels", "Панели и «Быстрые действия»", "Panels and quick actions", True),
    ("quick", "Быстрый доступ", "Quick Ask", "Окно Alt+Space: раскладка, свои команды и сценарии", "The Alt+Space window: layout, custom commands and routines", True),
    ("characters", "Персонажи", "Characters", "Персонажи и их карточки", "Characters and their cards", True),
    ("companion", "Компаньон и голос", "Companion & voice", "Свои модели компаньона и записанные голоса", "Your companion models and recorded voices", True),
    ("telegram", "Telegram", "Telegram", "Привязка и состояние бота (без токена)", "Bot pairing and state (without the token)", True),
    ("proxy", "Proxy", "Proxy", "Настройки Proxy, каталог моделей и статистика (без ключей)", "Proxy settings, model catalogue and statistics (without keys)", True),
    ("history", "История Work", "Work history", "Контрольные точки и артефакты MemuCode — могут быть большими",
     "MemuCode checkpoints and artifacts — can be large", False),
    ("memunote", "MemuNote", "MemuNote", "Настройки редактора, недавние файлы, открытые вкладки и несохранённые черновики",
     "Editor settings, recent files, open tabs and unsaved drafts", True),
    ("other", "Прочее", "Other", "Остальные файлы данных, в том числе новых функций", "Remaining data files, including new features", True),
    ("secrets", "Ключи и токены", "Keys & tokens", "Аккаунты провайдеров, ключи API, токены интеграций — только с шифрованием",
     "Provider accounts, API keys, integration tokens — encrypted backups only", False),
)
CATEGORY_IDS = tuple(row[0] for row in CATEGORIES)

EXCLUDED_DIRS = {"logs", "backups", "local-models", "voice-models", "wake-models", "dfy_browser", "dfy_shots",
                 "browser-profile", "browser-sessions", "visual-tests", "sync", "__pycache__", "cache", ".cache",
                 "caches", "tmp", "temp", "crashdumps", "gpucache", "code cache", "shadercache", "node_modules"}
RUNTIME_FILES = {"proxy.json", "memuai.json", "startup_guard.json", "safe_mode", "safe_mode.json",
                 "safe_mode_session.json", "model-info.json", "desktop.ini", "thumbs.db", ".ds_store"}
#: r29c: folders that are never user data although their names are ordinary: MemuNote's WebView2 profile
#: (cookies, caches, IndexedDB — a browser profile), the Explorer undo copies of Quick Ask (up to 512 MB of
#: deleted files with this PC's absolute paths), PDF export scratch, downloaded tools (cloudflared.exe).
EXCLUDED_PREFIXES = ("memunote/webview/", "memuai/quick_explorer/", "memuai/export-tmp/", "memuai/bin/")
#: Runtime files at a known place (a name too generic for RUNTIME_FILES).
RUNTIME_PATHS = {"memunote/instance.json"}
SECRET_FILES = {"accounts.json", "public.json", "integrations.json", "mcp.json", "panels-secrets.json",
                "dfy_vault.json", "dfy_vault.key", "cli_integrations.json", "secrets.json"}
#: Settings files that may hold a token next to ordinary settings: copied with those keys cut out.
REDACTED_FILES = {"preferences.json", "config.json", "telegram_state.json", "quick_endpoint.json",
                  "media-connections.json", "wake_word.json", "tgapp.json"}
_SECRET_KEY = re.compile(r"(^|_|-)(token|tokens|secret|secrets|password|passwd|api_?key|api_?keys|apikey|cookie|cookies|"
                         r"refresh_token|access_token|client_secret|private_key|bot_token|auth_key|pin|pin_hash|salt|"
                         r"credentials?)($|_|-)", re.I)

# (pattern on the path inside memuai/ without the profiles/<id>/ prefix, category); first match wins.
_RULES: Tuple[Tuple[str, str], ...] = (
    ("preferences.json", "settings"), ("profiles.json", "settings"), ("themes/*", "settings"),
    ("assistant.png", "settings"), ("assistant.ico", "settings"), ("wake_word.json", "settings"),
    ("focus.json", "settings"), ("pricing-overrides.json", "settings"), ("titlebar*.json", "settings"),
    ("chats.json", "chats"), ("quick_chats.json", "chats"), ("quick_history.json", "chats"), ("chat_*", "chats"),
    ("generated_media/*", "chats"),
    ("memory*.json", "memory"), ("reflect*.json", "memory"), ("memory/*", "memory"),
    ("prompts*.json", "prompts"), ("code_skills/*", "prompts"), ("skills*", "prompts"),
    ("schedule*.json", "schedules"),
    ("checkpoints/*", "history"), ("code_artifacts/*", "history"),
    ("workspaces.json", "work"), ("code_*", "work"), ("memucode*", "work"), ("agents*.json", "work"),
    # The «Characters» module keeps its cards and chats in the generic module store (r29c: they were «modules»).
    ("modules/store/characters/*", "characters"), ("modules/persist/characters.json", "characters"),
    ("modules/*", "modules"),
    ("panels*", "panels"), ("quick_canvas*", "panels"), ("quick_actions*", "panels"),
    ("quick_*", "quick"), ("assistant_*", "quick"), ("routines*", "quick"), ("commands*", "quick"),
    ("character*", "characters"), ("characters/*", "characters"),
    ("companion/*", "companion"), ("voice_clips/*", "companion"),
    ("telegram*", "telegram"), ("tgapp*", "telegram"),
)
_TOP_RULES: Tuple[Tuple[str, str], ...] = (
    ("config.json", "proxy"), ("models.json", "proxy"), ("metrics.json", "proxy"), ("detector-history.json", "proxy"),
    ("playground/*", "chats"), ("memucode/*", "work"), ("memunote/*", "memunote"),
)


class SyncError(RuntimeError):
    def __init__(self, ru: str, en: str = "", *, code: str = "") -> None:
        super().__init__(ru)
        self.ru = ru
        self.en = en or ru
        self.code = code


def _now_iso(ts: float | None = None) -> str:
    """UTC ISO-8601 built field by field (independent of locale/strftime quirks)."""
    d = datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc)
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}T{d.hour:02d}:{d.minute:02d}:{d.second:02d}Z"


def _err(exc: BaseException) -> Dict[str, str]:
    ru = getattr(exc, "ru", "") or str(exc) or type(exc).__name__
    en = getattr(exc, "en", "") or ru
    return {"ru": str(ru)[:400], "en": str(en)[:400], "code": str(getattr(exc, "code", "") or "")}


# ============================================================ classification

def _match(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path, pattern) or (pattern.endswith("/*") and path.startswith(pattern[:-1]))


def classify(rel: str) -> Optional[str]:
    """Category of a data-dir relative POSIX path, or ``None`` when it is never backed up."""
    rel = rel.replace("\\", "/").strip("/")
    if not rel:
        return None
    parts = rel.split("/")
    lowered = [p.lower() for p in parts]
    name = lowered[-1]
    if any(p in EXCLUDED_DIRS for p in lowered[:-1]):
        return None
    joined = "/".join(lowered)
    if joined in RUNTIME_PATHS or any(joined.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
        return None
    if name in RUNTIME_FILES or name.endswith((".lock", ".tmp", ".partial", ".part", ".crdownload", ".pyc")) \
            or ".tmp-" in name or ".corrupt-" in name or "cache" in name or name.startswith("~$"):
        return None
    if name in SECRET_FILES or name.endswith((".key", ".pem")):
        return "secrets"
    if lowered[0] != "memuai":
        sub = "/".join(lowered)
        for pattern, category in _TOP_RULES:
            if _match(sub, pattern):
                return category
        return "other"
    inner = lowered[1:]
    if len(inner) >= 3 and inner[0] == "profiles":
        inner = inner[2:]
    sub = "/".join(inner)
    if not sub:
        return None
    for pattern, category in _RULES:
        if _match(sub, pattern):
            return category
    return "other"


def profile_of(rel: str) -> str:
    parts = rel.split("/")
    if len(parts) >= 4 and parts[0] == "memuai" and parts[1] == "profiles":
        return parts[2]
    return "default"


def _safe_rel(rel: str) -> str:
    """A manifest path that is safe to write under the data root, or ``""``."""
    value = str(rel or "").replace("\\", "/")
    if not value or value.startswith("/") or re.match(r"^[A-Za-z]:", value) or "\x00" in value:
        return ""
    # Split by hand: PurePosixPath collapses "a//b" and "a/./b", which would hide a malformed entry.
    parts = value.split("/")
    if any(p in ("..", ".", "") for p in parts) or len(value) > 400:
        return ""
    return "/".join(parts)


# ============================================================== redaction

def _secret_key(key: str) -> bool:
    return bool(_SECRET_KEY.search(str(key)))


def _is_secret_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value) and all(isinstance(v, (str, dict)) for v in value)
    if isinstance(value, dict):
        return bool(value)
    return False


def redact_json(data: Any) -> Tuple[Any, List[List[str]]]:
    """Copy of ``data`` without secret-looking keys; returns the removed key paths."""
    removed: List[List[str]] = []

    def walk(node: Any, path: List[str]) -> Any:
        if isinstance(node, dict):
            out = {}
            for key, value in node.items():
                if _secret_key(key) and _is_secret_value(value):
                    removed.append(path + [str(key)])
                    continue
                out[key] = walk(value, path + [str(key)])
            return out
        if isinstance(node, list):
            return [walk(item, path + ["*"]) if isinstance(item, dict) else item for item in node]
        return node
    return walk(data, []), removed


def _merge_redacted(snapshot: Any, local: Any, paths: List[List[str]]) -> Any:
    """Put the local values of ``paths`` back into restored settings (a token the copy never had)."""
    for path in paths:
        if "*" in path:
            continue  # inside lists: rows may not line up; the restored row stays without the key
        src, dst = local, snapshot
        ok = True
        for key in path[:-1]:
            if not (isinstance(src, dict) and isinstance(dst, dict) and key in src):
                ok = False
                break
            src, dst = src[key], dst.setdefault(key, {}) if isinstance(dst.get(key, {}), dict) else dst[key]
        if ok and isinstance(src, dict) and isinstance(dst, dict) and path[-1] in src:
            dst[path[-1]] = src[path[-1]]
    return snapshot


# =============================================================== targets

class FolderTarget:
    """Backups in a local folder — e.g. the «Google Drive for desktop» folder that syncs by itself."""

    kind = "folder"

    def __init__(self, base: str | os.PathLike[str], *, cancel: threading.Event | None = None) -> None:
        self.base = Path(base)
        self.folder = self.base / "MemuAI Backups"
        self.cancel = cancel or threading.Event()

    def describe(self) -> str:
        return str(self.folder)

    def _ensure(self) -> None:
        if not self.base.is_dir():
            raise SyncError(f"Папка недоступна: {self.base}", f"Folder is not available: {self.base}", code="folder_missing")
        self.folder.mkdir(parents=True, exist_ok=True)

    def list(self, *, refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        self._ensure()
        out = {}
        for entry in os.scandir(self.folder):
            if entry.is_file() and entry.name.startswith("memuai-") and not entry.name.endswith(".partial"):
                stat = entry.stat()
                out[entry.name] = {"id": entry.name, "size": stat.st_size, "modified": _now_iso(stat.st_mtime)}
        return out

    def _path(self, name: str) -> Path:
        if "/" in name or "\\" in name or not name.startswith("memuai-"):
            raise SyncError("Недопустимое имя файла копии", "Invalid backup file name")
        return self.folder / name

    def put_bytes(self, name: str, data: bytes) -> None:
        self._ensure()
        tmp = self._path(name).with_name(name + ".partial")
        tmp.write_bytes(data)
        os.replace(tmp, self._path(name))

    def put_file(self, name: str, path: Path, progress: Callable[[int], None] | None = None) -> None:
        self._ensure()
        tmp = self._path(name).with_name(name + ".partial")
        with open(path, "rb") as reader, open(tmp, "wb") as writer:
            while True:
                if self.cancel.is_set():
                    raise SyncError("Операция отменена", "Cancelled", code="cancelled")
                chunk = reader.read(4 * 1024 * 1024)
                if not chunk:
                    break
                writer.write(chunk)
                if progress:
                    progress(len(chunk))
        os.replace(tmp, self._path(name))

    def get_bytes(self, name: str) -> bytes:
        try:
            return self._path(name).read_bytes()
        except FileNotFoundError as exc:
            raise SyncError(f"Файл {name} не найден", f"File {name} not found", code="not_found") from exc

    def get_file(self, name: str, dest: Path, progress: Callable[[int], None] | None = None) -> None:
        try:
            shutil.copyfile(self._path(name), dest)
        except FileNotFoundError as exc:
            raise SyncError(f"Файл {name} не найден", f"File {name} not found", code="not_found") from exc
        if progress:
            progress(dest.stat().st_size)

    def delete(self, name: str) -> None:
        try:
            self._path(name).unlink()
        except FileNotFoundError:
            pass


def detect_folders() -> List[Dict[str, str]]:
    """Folders of desktop sync clients (Google Drive for desktop first) that exist on this PC."""
    found: List[Dict[str, str]] = []
    seen = set()

    def add(path: str, kind: str, ru: str, en: str) -> None:
        try:
            resolved = str(Path(path))
            if resolved.lower() in seen or not Path(path).is_dir():
                return
        except OSError:
            return
        seen.add(resolved.lower())
        found.append({"path": resolved, "kind": kind, "ru": ru, "en": en})

    if os.name == "nt":  # pragma: no cover - drive letters exist only on Windows
        for letter in "GHIJKLMNOPQRSTUVWXYZDEF":
            for name in ("My Drive", "Мой диск"):
                add(f"{letter}:\\{name}", "gdrive", "Google Диск (Google Drive для компьютера)", "Google Drive for desktop")
    home = os.path.expanduser("~")
    for name in ("Google Drive", "My Drive", "Google Диск", os.path.join("Google Drive", "My Drive"),
                 os.path.join("Google Drive", "Мой диск")):
        add(os.path.join(home, name), "gdrive", "Google Диск (Google Drive для компьютера)", "Google Drive for desktop")
    for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if os.environ.get(env):
            add(os.environ[env], "onedrive", "OneDrive", "OneDrive")
    add(os.path.join(home, "OneDrive"), "onedrive", "OneDrive", "OneDrive")
    add(os.path.join(home, "Dropbox"), "dropbox", "Dropbox", "Dropbox")
    add(os.path.join(home, "Yandex.Disk"), "yandex", "Яндекс Диск", "Yandex Disk")
    return found


# ================================================================ service

DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "target": "drive",              # drive | folder
    "drive_space": "appdata",       # appdata (hidden app folder) | folder (visible «MemuAI Backups»)
    "folder_path": "",
    "interval_minutes": 1440,
    "at_time": "",                  # "HH:MM" for daily and longer intervals
    "on_exit": True,
    "only_changed": True,
    "keep_versions": 14,
    "keep_days": 0,
    "categories": {row[0]: row[5] for row in CATEGORIES},
    "include_secrets": False,
    "encrypt": False,
    "max_file_mb": 100,
    "bandwidth_kbps": 0,
    "device_name": "",
    "exit_timeout_s": 60,
    "client_id": "",
    "notify_errors": True,
    "exclude_patterns": [],
}
INTERVAL_PRESETS = (15, 30, 60, 180, 360, 720, 1440, 4320, 10080)


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default
    return max(low, min(high, number))


def normalize_settings(raw: Any) -> Dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    out: Dict[str, Any] = {}
    out["enabled"] = src.get("enabled", DEFAULTS["enabled"]) is True
    out["target"] = "folder" if src.get("target") == "folder" else "drive"
    out["drive_space"] = "folder" if src.get("drive_space") == "folder" else "appdata"
    out["folder_path"] = str(src.get("folder_path") or "").strip()[:500]
    out["interval_minutes"] = _int(src.get("interval_minutes"), DEFAULTS["interval_minutes"], 15, 43200)
    at = str(src.get("at_time") or "").strip()
    out["at_time"] = at if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at) else ""
    out["on_exit"] = src.get("on_exit", DEFAULTS["on_exit"]) is not False
    out["only_changed"] = src.get("only_changed", DEFAULTS["only_changed"]) is not False
    out["keep_versions"] = _int(src.get("keep_versions"), DEFAULTS["keep_versions"], 1, 500)
    out["keep_days"] = _int(src.get("keep_days"), DEFAULTS["keep_days"], 0, 3650)
    cats = src.get("categories") if isinstance(src.get("categories"), dict) else {}
    out["categories"] = {cid: (cats.get(cid) is True if cid in cats else DEFAULTS["categories"][cid]) for cid in CATEGORY_IDS}
    out["encrypt"] = src.get("encrypt") is True
    out["include_secrets"] = src.get("include_secrets") is True
    out["categories"]["secrets"] = out["include_secrets"]
    out["max_file_mb"] = _int(src.get("max_file_mb"), DEFAULTS["max_file_mb"], 1, 2048)
    out["bandwidth_kbps"] = _int(src.get("bandwidth_kbps"), 0, 0, 1_000_000)
    out["device_name"] = re.sub(r"[\x00-\x1f]", "", str(src.get("device_name") or ""))[:60].strip()
    out["exit_timeout_s"] = _int(src.get("exit_timeout_s"), DEFAULTS["exit_timeout_s"], 5, 600)
    out["client_id"] = str(src.get("client_id") or "").strip()[:300]
    out["notify_errors"] = src.get("notify_errors", True) is not False
    patterns = src.get("exclude_patterns") if isinstance(src.get("exclude_patterns"), list) else []
    out["exclude_patterns"] = [str(p).strip().replace("\\", "/")[:200] for p in patterns if str(p).strip()][:50]
    return out


class SyncService:
    """One per process.  Thread-safe; heavy work runs in the caller's thread."""

    def __init__(self, root: str | os.PathLike[str] | None = None, *, role: str = "memuai",
                 transport: Any = None, open_browser: Callable[[str], Any] | None = None) -> None:
        if root is None:
            from .config import DATA_DIR
            root = DATA_DIR
        self.root = Path(root)
        self.dir = self.root / "memuai" / "sync"
        self.role = role
        self.transport = transport
        self.store = crypto.SecretStore(self.dir)
        self._run_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.progress: Dict[str, Any] = {}
        self.notify: Callable[[Dict[str, str]], None] | None = None
        self.paused_reason: Callable[[], str] | None = None
        self._scheduler: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._folder_cache: Dict[str, str] = {}
        self._headers: Optional[Dict[str, Any]] = None
        from .gdrive_sync_drive import DriveAuth
        self.auth = DriveAuth(self.store, client_override=self._client, transport=transport, open_browser=open_browser)
        self.auth.on_connected = self._on_connected

    # ------------------------------------------------------------ files
    def _json(self, name: str, default: Any) -> Any:
        try:
            return json.loads((self.dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def _write_json(self, name: str, value: Any) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f"{name}.tmp-{os.getpid()}-{threading.get_ident()}"
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
        for attempt in range(8):
            try:
                os.replace(tmp, self.dir / name)
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.02 * (attempt + 1))

    # --------------------------------------------------------- settings
    def settings(self) -> Dict[str, Any]:
        return normalize_settings(self._json("settings.json", {}))

    def public_settings(self) -> Dict[str, Any]:
        out = self.settings()
        out["has_client_secret"] = self.store.has("client_secret")
        out["has_passphrase"] = self.store.has("passphrase")
        return out

    def save_settings(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(updates, dict):
            raise SyncError("Некорректные настройки", "Invalid settings")
        with self._state_lock:
            current = self.settings()
            merged = dict(current)
            for key, value in updates.items():
                if key in DEFAULTS:
                    if key == "categories" and isinstance(value, dict):
                        merged["categories"] = {**current["categories"], **value}
                    else:
                        merged[key] = value
            if "client_secret" in updates:
                self.store.set("client_secret", str(updates.get("client_secret") or "").strip()[:300])
            if "passphrase" in updates:
                phrase = str(updates.get("passphrase") or "")
                if phrase and len(phrase) < 8:
                    raise SyncError("Пароль шифрования — не короче 8 символов", "The passphrase must be at least 8 characters")
                self._set_passphrase(phrase)
            normalized = normalize_settings(merged)
            if normalized["encrypt"] and not self.store.has("passphrase"):
                raise SyncError("Для шифрования задайте пароль", "Set a passphrase to enable encryption", code="no_passphrase")
            if normalized["include_secrets"] and not normalized["encrypt"]:
                raise SyncError("Ключи и токены сохраняются только в зашифрованных копиях — включите шифрование",
                                "Keys and tokens are only backed up encrypted — turn on encryption", code="secrets_need_encryption")
            if normalized["target"] == "folder" and normalized["folder_path"]:
                self._check_folder(normalized["folder_path"])
            if current.get("client_id") != normalized["client_id"] and self.auth.connected():
                self.auth.disconnect(revoke=False)  # a token belongs to the client that issued it
            self._write_json("settings.json", normalized)
        return self.public_settings()

    def _check_folder(self, value: str) -> None:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise SyncError("Укажите полный путь к папке", "Enter the full folder path", code="folder_relative")
        try:
            resolved = path.resolve()
            root = self.root.resolve()
        except OSError:
            return
        if resolved == root or root in resolved.parents:
            raise SyncError("Папка копий не может лежать внутри папки данных MemuAI",
                            "The backup folder cannot be inside the MemuAI data folder", code="folder_inside")

    def _client(self) -> Tuple[str, str]:
        settings = self.settings()
        return settings["client_id"], self.store.get("client_secret")

    def _on_connected(self, info: Dict[str, Any]) -> None:
        with self._state_lock:
            state = self.state()
            state["account"] = {k: info.get(k) for k in ("email", "name", "quota_limit", "quota_usage")}
            state["account_checked"] = time.time()
            self._write_state(state)
        self._folder_cache.clear()

    # ----------------------------------------------------------- state
    def state(self) -> Dict[str, Any]:
        data = self._json("state.json", {})
        return data if isinstance(data, dict) else {}

    def _write_state(self, state: Dict[str, Any]) -> None:
        self._write_json("state.json", state)

    def _update_state(self, **values: Any) -> Dict[str, Any]:
        with self._state_lock:
            state = self.state()
            state.update(values)
            self._write_state(state)
            return state

    def device(self) -> Dict[str, str]:
        data = self._json("device.json", {})
        if not isinstance(data, dict) or not re.fullmatch(r"[a-z0-9]{8}", str(data.get("id") or "")):
            data = {"id": secrets.token_hex(4), "created": _now_iso()}
            self._write_json("device.json", data)
        name = self.settings()["device_name"] or socket.gethostname() or "PC"
        return {"id": data["id"], "name": name[:60]}

    # ---------------------------------------------------------- encryption
    def _set_passphrase(self, phrase: str) -> None:
        if not phrase:
            self.store.delete("passphrase")
            self.store.delete("active_key")
            return
        keys, record = crypto.new_key_record(phrase)
        self.store.set("passphrase", phrase)
        self.store.set("active_key", json.dumps(record))
        self.store.set("key:" + keys.key_id, base64.b64encode(keys.enc + keys.mac).decode("ascii"))

    def _active_keys(self) -> Tuple[Optional[crypto.Keys], Optional[Dict[str, Any]]]:
        raw = self.store.get("active_key")
        if not raw:
            return None, None
        record = json.loads(raw)
        material = self.store.get("key:" + record["key_id"])
        if material:
            blob = base64.b64decode(material)
            keys = crypto.Keys(blob[:32], blob[32:], base64.b64decode(record["salt"]))
            if keys.key_id == record["key_id"]:
                return keys, record
        keys = crypto.keys_from_record(self.store.get("passphrase"), record)
        return keys, record

    def _keys_for(self, key_id: str, target: Any, passphrase: str = "") -> crypto.Keys:
        material = self.store.get("key:" + key_id) if key_id else ""
        if material:
            blob = base64.b64decode(material)
            keys = crypto.Keys(blob[:32], blob[32:], b"")
            if keys.key_id == key_id:
                return keys
        records = self._remote_keys(target)
        record = next((row for row in records if row.get("key_id") == key_id), None)
        if record is None:
            raise SyncError("Нет сведений о ключе этой копии", "The key record of this backup is missing", code="no_key")
        phrase = passphrase or self.store.get("passphrase")
        if not phrase:
            raise SyncError("Копия зашифрована — введите пароль", "The backup is encrypted — enter the passphrase", code="need_passphrase")
        try:
            keys = crypto.keys_from_record(phrase, record)
        except crypto.CryptoError as exc:
            raise SyncError("Неверный пароль для этой копии", "Wrong passphrase for this backup", code="need_passphrase") from exc
        self.store.set("key:" + keys.key_id, base64.b64encode(keys.enc + keys.mac).decode("ascii"))
        return keys

    def _remote_keys(self, target: Any) -> List[Dict[str, Any]]:
        try:
            data = json.loads(target.get_bytes("memuai-keys.json").decode("utf-8"))
        except Exception:
            return []
        rows = data.get("keys") if isinstance(data, dict) else []
        return [row for row in rows or [] if isinstance(row, dict) and row.get("key_id")]

    def _publish_key(self, target: Any, record: Dict[str, Any]) -> None:
        rows = self._remote_keys(target)
        if any(row.get("key_id") == record["key_id"] for row in rows):
            return
        rows.append(record)
        target.put_bytes("memuai-keys.json", json.dumps({"format": FORMAT, "keys": rows[-50:]}).encode("utf-8"))

    # -------------------------------------------------------------- target
    def target(self, settings: Dict[str, Any] | None = None) -> Any:
        settings = settings or self.settings()
        if settings["target"] == "folder":
            if not settings["folder_path"]:
                raise SyncError("Не выбрана папка для копий", "No backup folder selected", code="no_folder")
            return FolderTarget(settings["folder_path"], cancel=self.cancel_event)
        from .gdrive_sync_drive import DriveTarget
        if not self.auth.connected():
            raise SyncError("Google Drive не подключён", "Google Drive is not connected", code="not_connected")
        return DriveTarget(self.auth, space=settings["drive_space"], folder_id_cache=self._folder_cache,
                           throttle_kbps=settings["bandwidth_kbps"], cancel=self.cancel_event)

    # --------------------------------------------------------------- scan
    def scan(self, settings: Dict[str, Any] | None = None, *, all_categories: bool = False) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Files to back up and files skipped because of the size limit."""
        settings = settings or self.settings()
        limit = settings["max_file_mb"] * 1024 * 1024
        wanted = {cid for cid, on in settings["categories"].items() if on}
        if not settings["include_secrets"]:
            wanted.discard("secrets")
        files: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        root = self.root
        if not root.is_dir():
            return files, skipped
        for current, dirs, names in os.walk(root):
            rel_dir = os.path.relpath(current, root).replace("\\", "/")
            rel_dir = "" if rel_dir == "." else rel_dir
            dirs[:] = [d for d in dirs if d.lower() not in EXCLUDED_DIRS and not os.path.islink(os.path.join(current, d))
                       and not any(f"{rel_dir}/{d}/".lstrip("/").lower().startswith(p) for p in EXCLUDED_PREFIXES)]
            for name in names:
                rel = f"{rel_dir}/{name}" if rel_dir else name
                category = classify(rel)
                if category is None or (not all_categories and category not in wanted):
                    continue
                if any(fnmatch.fnmatch(rel.lower(), p.lower()) for p in settings["exclude_patterns"]):
                    continue
                path = Path(current) / name
                try:
                    if path.is_symlink():
                        continue
                    stat = path.stat()
                except OSError:
                    continue
                row = {"rel": rel, "path": path, "size": stat.st_size, "mtime": stat.st_mtime_ns, "cat": category}
                if stat.st_size > limit:
                    skipped.append(row)
                    continue
                files.append(row)
        files.sort(key=lambda r: r["rel"])
        return files, skipped

    def category_stats(self) -> Dict[str, Any]:
        settings = self.settings()
        files, skipped = self.scan(settings, all_categories=True)
        stats = {cid: {"files": 0, "bytes": 0} for cid in CATEGORY_IDS}
        for row in files:
            stats[row["cat"]]["files"] += 1
            stats[row["cat"]]["bytes"] += row["size"]
        return {"categories": stats,
                "skipped": [{"rel": r["rel"], "size": r["size"], "cat": r["cat"]} for r in skipped[:50]],
                "limit_mb": settings["max_file_mb"]}

    def _read_for_backup(self, row: Dict[str, Any], include_secrets: bool) -> Tuple[bytes, List[List[str]]]:
        data = row["path"].read_bytes()
        name = row["rel"].rsplit("/", 1)[-1].lower()
        if include_secrets or name not in REDACTED_FILES:
            return data, []
        try:
            parsed = json.loads(data.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError):
            return data, []
        cleaned, removed = redact_json(parsed)
        if not removed:
            return data, []
        return json.dumps(cleaned, ensure_ascii=False, indent=1).encode("utf-8"), removed

    # ---------------------------------------------------------- run lock
    def _lock_path(self) -> Path:
        return self.dir / "run.lock"

    def _acquire(self) -> bool:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._lock_path()
        for _ in range(2):
            try:
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                try:
                    data = json.loads(path.read_text(encoding="utf-8") or "{}")
                except (OSError, ValueError):
                    data = {}
                stale = time.time() - float(data.get("started") or 0) > 12 * 3600 or not _pid_alive(int(data.get("pid") or 0))
                if stale:
                    try:
                        path.unlink()
                    except OSError:
                        return False
                    continue
                return False
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "role": self.role, "started": time.time()}, handle)
            return True
        return False

    def _release(self) -> None:
        try:
            self._lock_path().unlink()
        except OSError:
            pass

    def busy(self) -> Optional[Dict[str, Any]]:
        if self._run_lock.locked():
            return dict(self.progress) or {"phase": "start"}
        try:
            data = json.loads(self._lock_path().read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            return None
        if data and _pid_alive(int(data.get("pid") or 0)) and time.time() - float(data.get("started") or 0) < 12 * 3600:
            shared = self.state().get("progress") or {}
            return {"phase": shared.get("phase") or "other", "role": data.get("role"), **shared}
        return None

    def _progress(self, **values: Any) -> None:
        self.progress.update(values)
        self.progress["updated"] = time.time()
        # Shared with the other process (Proxy ↔ MemuAI) at most twice a second.
        if time.time() - float(self.progress.get("_written") or 0) > 0.5 or values.get("phase") in ("done", "error"):
            self.progress["_written"] = time.time()
            try:
                self._update_state(progress={k: v for k, v in self.progress.items() if not k.startswith("_")})
            except OSError:
                pass

    # ------------------------------------------------------------- backup
    def backup(self, reason: str = "manual", *, label: str = "", force: bool = False) -> Dict[str, Any]:
        """Make one snapshot. ``force`` ignores «only when changed»."""
        if not self._run_lock.acquire(blocking=False):
            raise SyncError("Резервное копирование уже идёт", "A backup is already running", code="busy")
        try:
            if not self._acquire():
                raise SyncError("Резервное копирование уже идёт в другом окне MemuAI или в Proxy",
                                "A backup is already running in another MemuAI process", code="busy")
            try:
                self.cancel_event.clear()
                self.progress = {"phase": "scan", "kind": "backup", "reason": reason, "started": time.time(),
                                 "done": 0, "total": 0}
                started = time.time()
                self._update_state(last_attempt=started)
                try:
                    result = self._backup(reason, label=label, force=force)
                except BaseException as exc:
                    error = _err(exc)
                    with self._state_lock:
                        state = self.state()
                        state["last_error"] = {**error, "ts": time.time(), "reason": reason}
                        state["failures"] = int(state.get("failures") or 0) + 1
                        state["progress"] = {}
                        self._history(state, {"ts": started, "kind": "backup", "reason": reason, "ok": False,
                                              "error": error, "seconds": round(time.time() - started, 1)})
                        self._write_state(state)
                    self.progress = {}
                    if self.notify and reason != "manual" and self.settings()["notify_errors"]:
                        try:
                            self.notify(error)
                        except Exception:
                            pass
                    raise
                with self._state_lock:
                    state = self.state()
                    state["last_error"] = None
                    state["failures"] = 0
                    state["progress"] = {}
                    state["last_success"] = time.time()
                    state["last_result"] = result
                    self._history(state, {"ts": started, "kind": "backup", "reason": reason, "ok": True,
                                          "unchanged": result.get("unchanged", False), "id": result.get("id", ""),
                                          "new_bytes": result.get("new_bytes", 0), "seconds": round(time.time() - started, 1)})
                    self._write_state(state)
                self.progress = {}
                return result
            finally:
                self._release()
        finally:
            self._run_lock.release()

    @staticmethod
    def _history(state: Dict[str, Any], row: Dict[str, Any]) -> None:
        rows = state.get("history") if isinstance(state.get("history"), list) else []
        rows.insert(0, row)
        state["history"] = rows[:HISTORY_KEEP]

    def _backup(self, reason: str, *, label: str, force: bool) -> Dict[str, Any]:
        settings = self.settings()
        target = self.target(settings)
        keys, record = (None, None)
        if settings["encrypt"]:
            keys, record = self._active_keys()
            if keys is None:
                raise SyncError("Шифрование включено, но пароль не задан", "Encryption is on but no passphrase is set",
                                code="no_passphrase")
        include_secrets = settings["include_secrets"] and keys is not None
        if settings["include_secrets"] and keys is None:
            settings["categories"]["secrets"] = False
            settings["include_secrets"] = False
        device = self.device()
        key_id = keys.key_id if keys else ""
        files, skipped = self.scan(settings)
        self._progress(phase="index", total=sum(r["size"] for r in files), files=len(files))
        remote = target.list(refresh=True)
        index = self._load_index(target, remote, device["id"])
        hash_cache = self._json("hashes.json", {})
        if not isinstance(hash_cache, dict):
            hash_cache = {}
        new_cache: Dict[str, Any] = {}
        work = Path(tempfile.mkdtemp(prefix="pack-", dir=str(self._tmp_dir())))
        packs: List[Dict[str, Any]] = []
        manifest_files: Dict[str, Dict[str, Any]] = {}
        pending: Dict[str, str] = {}     # sha → pack name of this run
        writer: Optional[zipfile.ZipFile] = None
        current: Dict[str, Any] = {}
        d = datetime.now(timezone.utc)
        # The 4-hex suffix starts with the milliseconds (3 hex digits) so ids of one second still sort by time;
        # the last digit is random. Retention relies on "newest first" being plain string order.
        snap_id = (f"{d.year:04d}{d.month:02d}{d.day:02d}T{d.hour:02d}{d.minute:02d}{d.second:02d}Z-"
                   f"{d.microsecond // 1000:03x}{secrets.token_hex(1)[0]}")

        def open_pack() -> None:
            nonlocal writer, current
            pack_id = f"{snap_id}-{len(packs) + 1}"
            name = f"memuai-pack-{device['id']}-{pack_id}.zip" + (".enc" if keys else "")
            path = work / f"pack-{len(packs) + 1}.zip"
            writer = zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6)
            current = {"name": name, "path": path, "bytes": 0, "files": 0}
            packs.append(current)

        try:
            done = 0
            for row in files:
                if self.cancel_event.is_set():
                    raise SyncError("Резервное копирование отменено", "The backup was cancelled", code="cancelled")
                cache_key = f"{row['rel']}|{row['size']}|{row['mtime']}|{int(include_secrets)}"
                cached = hash_cache.get(cache_key)
                sha, redacted = (cached.get("sha"), cached.get("redacted") or []) if isinstance(cached, dict) else ("", [])
                data: Optional[bytes] = None
                if not sha or (sha not in index and sha not in pending):
                    try:
                        data, redacted = self._read_for_backup(row, include_secrets)
                    except OSError:
                        continue  # vanished or locked file: next run takes it
                    sha = hashlib.sha256(data).hexdigest()
                new_cache[cache_key] = {"sha": sha, "redacted": redacted}
                entry = {"sha": sha, "size": len(data) if data is not None else row["size"], "cat": row["cat"],
                         "mtime": row["mtime"]}
                if redacted:
                    entry["redacted"] = redacted
                if sha in pending:
                    entry["pack"] = pending[sha]
                elif sha in index and index[sha].get("key") == key_id:
                    entry["pack"] = index[sha]["pack"]
                else:
                    if data is None:
                        data, redacted = self._read_for_backup(row, include_secrets)
                    if writer is None or current["bytes"] >= PACK_LIMIT:
                        if writer is not None:
                            writer.close()
                        open_pack()
                    compress = zipfile.ZIP_STORED if Path(row["rel"]).suffix.lower() in COMPRESSED_EXT else zipfile.ZIP_DEFLATED
                    writer.writestr(zipfile.ZipInfo(sha, date_time=(2020, 1, 1, 0, 0, 0)), data, compress_type=compress)
                    current["bytes"] += len(data)
                    current["files"] += 1
                    pending[sha] = current["name"]
                    entry["pack"] = current["name"]
                manifest_files[row["rel"]] = entry
                done += row["size"]
                self._progress(phase="pack", done=done)
            if writer is not None:
                writer.close()
            digest = _files_digest(manifest_files, settings, key_id)
            state = self.state()
            if not force and settings["only_changed"] and reason in ("auto", "exit", "start") \
                    and digest == state.get("last_digest") and state.get("last_snapshot") in _manifest_ids(remote, device["id"]):
                self._write_json("hashes.json", new_cache)
                return {"unchanged": True, "id": state.get("last_snapshot", ""), "files": len(manifest_files),
                        "bytes": sum(e["size"] for e in manifest_files.values()), "new_bytes": 0,
                        "skipped": len(skipped), "target": target.describe()}
            # Upload packs, then the manifest (commit point).
            if keys and record:
                self._publish_key(target, record)
            upload_total = 0
            for pack in packs:
                if keys:
                    encrypted = pack["path"].with_suffix(".enc")
                    crypto.encrypt_file(pack["path"], encrypted, keys)
                    pack["path"].unlink()
                    pack["path"] = encrypted
                pack["size"] = pack["path"].stat().st_size
                upload_total += pack["size"]
            self._progress(phase="upload", done=0, total=upload_total)
            sent = 0

            def progressed(n: int) -> None:
                nonlocal sent
                sent += n
                self._progress(done=min(sent, upload_total))
            referenced = sorted({e["pack"] for e in manifest_files.values()})
            uploaded: List[str] = []
            committed = False
            try:
                for pack in packs:
                    uploaded.append(pack["name"])   # before the call: a failed upload may still have left a file
                    target.put_file(pack["name"], pack["path"], progressed)
                totals = {"files": len(manifest_files), "bytes": sum(e["size"] for e in manifest_files.values()),
                          "new_files": sum(p["files"] for p in packs), "new_bytes": sum(p["bytes"] for p in packs),
                          "upload_bytes": upload_total, "skipped": len(skipped)}
                cats: Dict[str, int] = {}
                for entry in manifest_files.values():
                    cats[entry["cat"]] = cats.get(entry["cat"], 0) + 1
                from .version import APP_VERSION
                header = {"id": snap_id, "created": _now_iso(), "created_ts": time.time(), "device": device["name"],
                          "device_id": device["id"], "reason": reason, "label": str(label or "")[:120],
                          "app_version": APP_VERSION, "encrypted": bool(keys), "key_id": key_id,
                          "secrets": include_secrets, "categories": cats, "stats": totals, "packs": referenced,
                          "profiles": sorted({profile_of(rel) for rel in manifest_files})}
                body = {"files": manifest_files}
                document: Dict[str, Any] = {"format": FORMAT, "version": FORMAT_VERSION, "header": header}
                if keys:
                    document["body_enc"] = base64.b64encode(crypto.encrypt_bytes(json.dumps(body).encode("utf-8"), keys)).decode("ascii")
                else:
                    document["body"] = body
                self._progress(phase="commit")
                target.put_bytes(_manifest_name(device["id"], snap_id), json.dumps(document, ensure_ascii=False).encode("utf-8"))
                committed = True
            finally:
                if not committed:
                    # r29c: a run that fails before its manifest (Drive full, network gone, cancelled) leaves
                    # packs nothing references; on a full Drive retention never runs, so they would keep the
                    # quota exhausted. Delete them now (best effort — the next successful run's GC also does).
                    self._drop_uploaded(target, uploaded, manifest=_manifest_name(device["id"], snap_id))
            for sha, pack_name in pending.items():
                index[sha] = {"pack": pack_name, "key": key_id}
            self._save_index(index, device["id"])
            self._write_json("hashes.json", new_cache)
            self._update_state(last_digest=digest, last_snapshot=snap_id)
            self._progress(phase="cleanup")
            removed = self._retention(target, settings, device["id"], keep_id=snap_id, keep_packs=referenced)
            if target.kind == "drive":
                self._refresh_account_later()
            return {"unchanged": False, "id": snap_id, **totals, "removed": removed, "target": target.describe(),
                    "encrypted": bool(keys)}
        finally:
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    pass
            shutil.rmtree(work, ignore_errors=True)

    def _drop_uploaded(self, target: Any, names: List[str], *, manifest: str) -> None:
        if not names:
            return
        self.cancel_event.clear()   # a cancelled run still cleans up (the Drive target checks the same event)
        try:
            if manifest in target.list(refresh=True):
                return              # the manifest did arrive (only its answer was lost): the packs are in use
        except Exception:  # noqa: BLE001 - storage unreachable: GC of the next successful run deletes them
            return
        for name in names:
            try:
                target.delete(name)
            except Exception:  # noqa: BLE001 - the original error matters, not this one
                pass

    def _refresh_account_later(self) -> None:
        state = self.state()
        if time.time() - float(state.get("account_checked") or 0) < 6 * 3600:
            return
        try:
            info = self.auth.account_info()
            if info:
                self._on_connected(info)
        except Exception:
            pass

    def _tmp_dir(self) -> Path:
        path = self.dir / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        # Leftovers of a crashed run.
        for child in path.iterdir():
            try:
                if time.time() - child.stat().st_mtime > 6 * 3600:
                    shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink()
            except OSError:
                pass
        return path

    # --------------------------------------------------------------- index
    def _load_index(self, target: Any, remote: Dict[str, Any], device_id: str) -> Dict[str, Dict[str, str]]:
        """sha → {pack, key} for blobs this device already stored and still kept."""
        cache = self._json("index.json", {})
        target_key = self._target_key()
        if isinstance(cache, dict) and cache.get("target") == target_key and isinstance(cache.get("blobs"), dict):
            blobs = {sha: row for sha, row in cache["blobs"].items() if isinstance(row, dict) and row.get("pack") in remote}
            if blobs or not _manifest_ids(remote, device_id):
                return blobs
        # Rebuild from this device's manifests.
        blobs: Dict[str, Dict[str, str]] = {}
        for name in sorted(n for n in remote if n.startswith(f"memuai-manifest-{device_id}-")):
            try:
                header, body = self._read_manifest(target, name)
            except SyncError:
                continue
            for entry in body.get("files", {}).values():
                if entry.get("pack") in remote:
                    blobs[entry["sha"]] = {"pack": entry["pack"], "key": header.get("key_id", "")}
        return blobs

    def _save_index(self, blobs: Dict[str, Any], device_id: str) -> None:
        self._write_json("index.json", {"target": self._target_key(), "device": device_id, "blobs": blobs})

    def _target_key(self) -> str:
        s = self.settings()
        return f"{s['target']}|{s['drive_space'] if s['target'] == 'drive' else s['folder_path'].lower()}|{s['client_id']}"

    # ----------------------------------------------------------- manifests
    def _read_manifest(self, target: Any, name: str, passphrase: str = "") -> Tuple[Dict[str, Any], Dict[str, Any]]:
        try:
            document = json.loads(target.get_bytes(name).decode("utf-8"))
        except SyncError:
            raise
        except Exception as exc:
            raise SyncError("Описание копии повреждено", "The backup description is damaged", code="bad_manifest") from exc
        if not isinstance(document, dict) or document.get("format") != FORMAT:
            raise SyncError("Это не резервная копия MemuAI", "This is not a MemuAI backup", code="bad_manifest")
        if int(document.get("version") or 0) > FORMAT_VERSION:
            raise SyncError("Копия сделана более новой версией MemuAI — обновите программу",
                            "The backup was made by a newer MemuAI — update the app", code="newer")
        header = document.get("header") or {}
        if "body" in document:
            body = document["body"]
        else:
            keys = self._keys_for(str(header.get("key_id") or ""), target, passphrase)
            try:
                body = json.loads(crypto.decrypt_bytes(base64.b64decode(document.get("body_enc") or ""), keys).decode("utf-8"))
            except crypto.CryptoError as exc:
                raise SyncError(exc.ru, exc.en, code="need_passphrase") from exc
        if not isinstance(body, dict) or not isinstance(body.get("files"), dict):
            raise SyncError("Описание копии повреждено", "The backup description is damaged", code="bad_manifest")
        return header, body

    def _header(self, target: Any, name: str, meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The public header of a manifest, cached by name + size (manifests never change)."""
        cache = self._headers
        if cache is None:
            loaded = self._json("headers.json", {})
            cache = self._headers = loaded if isinstance(loaded, dict) else {}
        key = f"{self._target_key()}|{name}|{meta.get('size', 0)}"
        if key in cache:
            return cache[key]
        try:
            document = json.loads(target.get_bytes(name).decode("utf-8"))
            header = document.get("header") or {}
        except Exception:
            return None
        if not isinstance(header, dict):
            return None
        cache[key] = header
        if len(cache) > 2000:
            for stale in list(cache)[:500]:
                cache.pop(stale, None)
        try:
            self._write_json("headers.json", cache)
        except OSError:
            pass
        return header

    def list_snapshots(self) -> Dict[str, Any]:
        settings = self.settings()
        target = self.target(settings)
        remote = target.list(refresh=True)
        pins = self._pins(target)
        device = self.device()
        rows = []
        for name, meta in remote.items():
            if not name.startswith("memuai-manifest-"):
                continue
            header = self._header(target, name, meta)
            if header is None:
                continue
            missing = [p for p in header.get("packs") or [] if p not in remote]
            rows.append({"id": header.get("id"), "name": name, "created": header.get("created"),
                         "created_ts": header.get("created_ts"), "device": header.get("device"),
                         "device_id": header.get("device_id"), "own": header.get("device_id") == device["id"],
                         "reason": header.get("reason"), "label": header.get("label", ""),
                         "app_version": header.get("app_version"), "encrypted": bool(header.get("encrypted")),
                         "secrets": bool(header.get("secrets")), "categories": header.get("categories") or {},
                         "stats": header.get("stats") or {}, "pinned": header.get("id") in pins,
                         "complete": not missing, "manifest_bytes": meta.get("size", 0),
                         "stored_bytes": sum(remote[p]["size"] for p in header.get("packs") or [] if p in remote)})
        rows.sort(key=lambda r: float(r.get("created_ts") or 0), reverse=True)
        total = sum(meta.get("size", 0) for meta in remote.values())
        return {"snapshots": rows, "total_bytes": total, "target": target.describe(), "device_id": device["id"]}

    def _pins(self, target: Any) -> List[str]:
        try:
            data = json.loads(target.get_bytes("memuai-pins.json").decode("utf-8"))
            return [str(x) for x in data.get("pinned") or []]
        except Exception:
            return []

    def pin(self, snapshot_id: str, pinned: bool) -> List[str]:
        target = self.target()
        pins = [p for p in self._pins(target) if p != snapshot_id]
        if pinned:
            pins.append(snapshot_id)
        target.put_bytes("memuai-pins.json", json.dumps({"format": FORMAT, "pinned": pins[-200:]}).encode("utf-8"))
        return pins

    def _find_manifest(self, target: Any, snapshot_id: str) -> str:
        if not re.fullmatch(r"[0-9TZ]{16}-[0-9a-f]{4}", str(snapshot_id or "")):
            raise SyncError("Неизвестная копия", "Unknown backup", code="not_found")
        remote = target.list()
        for name in remote:
            if name.startswith("memuai-manifest-") and name.endswith(f"-{snapshot_id}.json"):
                return name
        remote = target.list(refresh=True)
        for name in remote:
            if name.startswith("memuai-manifest-") and name.endswith(f"-{snapshot_id}.json"):
                return name
        raise SyncError("Копия не найдена — возможно, её уже удалили", "Backup not found — it may have been deleted", code="not_found")

    def delete_snapshot(self, snapshot_id: str) -> Dict[str, Any]:
        with self._exclusive("delete"):
            settings = self.settings()
            target = self.target(settings)
            name = self._find_manifest(target, snapshot_id)
            device_id = name[len("memuai-manifest-"):].split("-", 1)[0]
            target.delete(name)
            if snapshot_id in self._pins(target):
                self.pin(snapshot_id, False)
            removed = self._gc(target, device_id)
            if self.state().get("last_snapshot") == snapshot_id:
                self._update_state(last_digest="", last_snapshot="")
            return {"ok": True, "removed_packs": removed}

    def _exclusive(self, what: str):
        service = self

        class _Ctx:
            def __enter__(self_inner):
                if not service._run_lock.acquire(blocking=False):
                    raise SyncError("Сейчас идёт другая операция синхронизации", "Another sync operation is running", code="busy")
                if not service._acquire():
                    service._run_lock.release()
                    raise SyncError("Сейчас идёт другая операция синхронизации", "Another sync operation is running", code="busy")
                service.cancel_event.clear()
                service.progress = {"phase": "start", "kind": what, "started": time.time(), "done": 0, "total": 0}
                return self_inner

            def __exit__(self_inner, *exc: Any) -> None:
                service._release()
                service.progress = {}
                try:
                    service._update_state(progress={})
                except OSError:
                    pass
                service._run_lock.release()
        return _Ctx()

    # ----------------------------------------------------------- retention
    def _retention(self, target: Any, settings: Dict[str, Any], device_id: str, *, keep_id: str = "",
                   keep_packs: Iterable[str] = ()) -> int:
        remote = target.list(refresh=True)
        pins = set(self._pins(target))
        own = []
        for name in remote:
            if name.startswith(f"memuai-manifest-{device_id}-") and name.endswith(".json"):
                own.append((name[len(f"memuai-manifest-{device_id}-"):-5], name))
        # ids start with a UTC timestamp (+ milliseconds): newest first; the run that just finished always wins a tie.
        own.sort(key=lambda row: (row[0][:20], row[0] == keep_id, row[0]), reverse=True)
        cutoff = time.time() - settings["keep_days"] * 86400 if settings["keep_days"] else None
        kept = 0
        deleted = 0
        for index, (snap, name) in enumerate(own):
            if snap == keep_id:
                kept += 1
                continue
            if snap in pins:
                continue
            too_many = kept >= settings["keep_versions"]
            too_old = cutoff is not None and _id_ts(snap) < cutoff and index > 0
            if too_many or too_old:
                target.delete(name)
                deleted += 1
            else:
                kept += 1
        return deleted + self._gc(target, device_id, keep_packs)

    def _gc(self, target: Any, device_id: str, keep_packs: Iterable[str] = ()) -> int:
        """Delete this device's packs that no remaining manifest references.

        ``keep_packs``: the packs of the snapshot just committed — never deleted even when a storage listing
        lags behind and does not show its manifest yet (r29c)."""
        remote = target.list(refresh=True)
        referenced: set = set(keep_packs)
        for name in remote:
            if not name.startswith(f"memuai-manifest-{device_id}-"):
                continue
            header = self._header(target, name, remote[name])
            if header is None:
                return 0  # never delete packs while a manifest cannot be read
            referenced.update(header.get("packs") or [])
        removed = 0
        for name in remote:
            if name.startswith(f"memuai-pack-{device_id}-") and name not in referenced:
                target.delete(name)
                removed += 1
        return removed

    # -------------------------------------------------------------- restore
    def preview_restore(self, snapshot_id: str, passphrase: str = "") -> Dict[str, Any]:
        target = self.target()
        name = self._find_manifest(target, snapshot_id)
        header, body = self._read_manifest(target, name, passphrase)
        cats: Dict[str, Dict[str, int]] = {}
        for rel, entry in body["files"].items():
            row = cats.setdefault(entry.get("cat") or "other", {"files": 0, "bytes": 0, "changed": 0})
            row["files"] += 1
            row["bytes"] += int(entry.get("size") or 0)
            local = self.root / rel
            try:
                if not local.is_file() or local.stat().st_size != int(entry.get("size") or 0) or entry.get("redacted"):
                    row["changed"] += 1
                elif hashlib.sha256(local.read_bytes()).hexdigest() != entry.get("sha"):
                    row["changed"] += 1
            except OSError:
                row["changed"] += 1
        return {"header": header, "categories": cats}

    def restore(self, snapshot_id: str, categories: Iterable[str] | None = None, *, passphrase: str = "",
                mirror: bool = False, profiles: Iterable[str] | None = None) -> Dict[str, Any]:
        with self._exclusive("restore"):
            settings = self.settings()
            target = self.target(settings)
            name = self._find_manifest(target, snapshot_id)
            header, body = self._read_manifest(target, name, passphrase)
            keys = self._keys_for(header.get("key_id", ""), target, passphrase) if header.get("encrypted") else None
            selected = self._select(body["files"], categories, profiles)
            if not selected:
                raise SyncError("В выбранных разделах копии нет файлов", "The selected categories of this backup have no files",
                                code="empty")
            work = Path(tempfile.mkdtemp(prefix="restore-", dir=str(self._tmp_dir())))
            try:
                blobs = self._fetch_blobs(target, selected, keys, work)
                return self._apply(selected, blobs, categories, mirror=mirror, profiles=profiles,
                                   source={"snapshot": snapshot_id, "created": header.get("created"),
                                           "device": header.get("device")})
            finally:
                shutil.rmtree(work, ignore_errors=True)

    def _select(self, files: Dict[str, Any], categories: Iterable[str] | None,
                profiles: Iterable[str] | None) -> Dict[str, Dict[str, Any]]:
        cats = set(categories or CATEGORY_IDS)
        profs = set(profiles) if profiles else None
        out = {}
        for rel, entry in files.items():
            safe = _safe_rel(rel)
            if not safe or classify(safe) is None or not isinstance(entry, dict):
                continue
            # The local classification decides: a manifest cannot smuggle accounts.json in as «settings».
            category = classify(safe)
            # A copy made before a file moved to another category (r29c: characters were «modules», generated
            # media «other») is still restored when either name is chosen; secrets are decided locally only.
            listed = str(entry.get("cat") or "")
            chosen = category in cats or (category != "secrets" and listed != "secrets" and listed in cats)
            if not chosen or not isinstance(entry.get("pack"), str):
                continue
            if profs is not None and profile_of(safe) not in profs:
                continue
            if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha") or "")):
                continue
            out[safe] = entry
        return out

    def _fetch_blobs(self, target: Any, selected: Dict[str, Dict[str, Any]], keys: Optional[crypto.Keys],
                     work: Path) -> Dict[str, Path]:
        need: Dict[str, set] = {}
        for entry in selected.values():
            need.setdefault(entry["pack"], set()).add(entry["sha"])
        remote = target.list(refresh=True)
        missing = [p for p in need if p not in remote]
        if missing:
            raise SyncError("Часть файлов копии отсутствует в хранилище — выберите другую копию",
                            "Some files of this backup are missing from the storage — choose another backup", code="incomplete")
        total = sum(remote[p]["size"] for p in need)
        self._progress(phase="download", done=0, total=total)
        got = 0

        def progressed(n: int) -> None:
            nonlocal got
            got += n
            self._progress(done=min(got, total))
        blobs: Dict[str, Path] = {}
        blob_dir = work / "blobs"
        blob_dir.mkdir()
        for index, (pack, shas) in enumerate(need.items()):
            raw = work / f"pack-{index}"
            target.get_file(pack, raw, progressed)
            zpath = raw
            if pack.endswith(".enc"):
                if keys is None:
                    raise SyncError("Копия зашифрована — введите пароль", "The backup is encrypted — enter the passphrase",
                                    code="need_passphrase")
                zpath = work / f"pack-{index}.zip"
                try:
                    crypto.decrypt_file(raw, zpath, keys)
                except crypto.CryptoError as exc:
                    raise SyncError(exc.ru, exc.en, code="need_passphrase") from exc
                raw.unlink()
            try:
                with zipfile.ZipFile(zpath) as archive:
                    for sha in shas:
                        out = blob_dir / sha
                        hasher = hashlib.sha256()
                        with archive.open(sha) as reader, open(out, "wb") as writer:
                            while True:
                                chunk = reader.read(1024 * 1024)
                                if not chunk:
                                    break
                                hasher.update(chunk)
                                writer.write(chunk)
                        if hasher.hexdigest() != sha:
                            raise SyncError("Файл копии повреждён (контрольная сумма)", "A backup file is damaged (checksum)",
                                            code="corrupt")
                        blobs[sha] = out
            except (zipfile.BadZipFile, KeyError) as exc:
                raise SyncError("Архив копии повреждён", "The backup archive is damaged", code="corrupt") from exc
            zpath.unlink()
        return blobs

    def _apply(self, selected: Dict[str, Dict[str, Any]], blobs: Dict[str, Path], categories: Iterable[str] | None,
               *, mirror: bool, profiles: Iterable[str] | None, source: Dict[str, Any]) -> Dict[str, Any]:
        cats = set(categories or CATEGORY_IDS)
        to_delete: List[str] = []
        if mirror:
            current, _ = self.scan(normalize_settings({**self.settings(), "categories": {c: True for c in CATEGORY_IDS},
                                                       "include_secrets": "secrets" in cats, "encrypt": True,
                                                       "max_file_mb": 2048}), all_categories=True)
            profs = set(profiles) if profiles else None
            for row in current:
                if row["cat"] in cats and row["rel"] not in selected and (profs is None or profile_of(row["rel"]) in profs):
                    to_delete.append(row["rel"])
        # 1. Safety copy of everything we are about to change.
        self._progress(phase="safety")
        safety = self._safety_backup(list(selected) + to_delete, source)
        # 2. Write.
        self._progress(phase="write", done=0, total=len(selected))
        written = 0
        errors: List[str] = []
        for index, (rel, entry) in enumerate(sorted(selected.items())):
            path = self.root / rel
            try:
                resolved_root = self.root.resolve()
                if resolved_root not in path.resolve().parents:
                    raise SyncError("Недопустимый путь", "Invalid path")
                data = blobs[entry["sha"]].read_bytes()
                if entry.get("redacted"):
                    data = self._merge_local_secrets(path, data, entry["redacted"])
                _atomic_write(path, data)
                written += 1
            except Exception as exc:  # one locked file must not stop the rest
                errors.append(f"{rel}: {_err(exc)['ru']}")
            self._progress(done=index + 1)
        removed = 0
        for rel in to_delete:
            try:
                (self.root / rel).unlink()
                removed += 1
            except OSError:
                pass
        report = {"ok": not errors, "written": written, "removed": removed, "errors": errors[:20], "safety": safety,
                  "source": source, "categories": sorted(cats), "restart": True, "ts": time.time()}
        with self._state_lock:
            state = self.state()
            state["last_restore"] = report
            self._history(state, {"ts": time.time(), "kind": "restore", "ok": not errors, "id": source.get("snapshot", ""),
                                  "files": written})
            self._write_state(state)
        return report

    def _merge_local_secrets(self, path: Path, data: bytes, paths: List[List[str]]) -> bytes:
        try:
            snapshot = json.loads(data.decode("utf-8"))
            local = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
        except (OSError, ValueError, UnicodeDecodeError):
            return data
        merged = _merge_redacted(snapshot, local, paths)
        return json.dumps(merged, ensure_ascii=False, indent=2).encode("utf-8")

    # ---------------------------------------------------------- safety zip
    def safety_dir(self) -> Path:
        path = self.dir / "safety"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _safety_backup(self, rels: List[str], source: Dict[str, Any]) -> str:
        d = datetime.now()
        name = f"before-restore-{d.year:04d}{d.month:02d}{d.day:02d}-{d.hour:02d}{d.minute:02d}{d.second:02d}-{secrets.token_hex(2)}.zip"
        path = self.safety_dir() / name
        existing, created = [], []
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for rel in rels:
                local = self.root / rel
                if local.is_file():
                    try:
                        archive.write(local, "files/" + rel)
                        existing.append(rel)
                    except OSError:
                        pass
                else:
                    created.append(rel)
            archive.writestr("safety.json", json.dumps({"format": "memuai-restore-safety", "created": _now_iso(),
                                                         "source": source, "files": existing, "created_files": created},
                                                        ensure_ascii=False, indent=1))
        old = sorted(self.safety_dir().glob("before-restore-*.zip"), reverse=True)
        for stale in old[SAFETY_KEEP:]:
            try:
                stale.unlink()
            except OSError:
                pass
        return name

    def list_safety(self) -> List[Dict[str, Any]]:
        rows = []
        for path in sorted(self.safety_dir().glob("before-restore-*.zip"), reverse=True):
            try:
                with zipfile.ZipFile(path) as archive:
                    meta = json.loads(archive.read("safety.json"))
            except Exception:
                meta = {}
            rows.append({"name": path.name, "size": path.stat().st_size, "created": meta.get("created"),
                         "source": meta.get("source") or {}, "files": len(meta.get("files") or [])})
        return rows

    def undo_restore(self, name: str) -> Dict[str, Any]:
        if not re.fullmatch(r"before-restore-[0-9-]+-[0-9a-f]{4}\.zip", str(name or "")):
            raise SyncError("Неизвестная страховочная копия", "Unknown safety copy", code="not_found")
        path = self.safety_dir() / name
        if not path.is_file():
            raise SyncError("Страховочная копия не найдена", "Safety copy not found", code="not_found")
        with self._exclusive("undo"):
            written = removed = 0
            with zipfile.ZipFile(path) as archive:
                meta = json.loads(archive.read("safety.json"))
                for rel in meta.get("files") or []:
                    safe = _safe_rel(rel)
                    if not safe or classify(safe) is None:
                        continue
                    _atomic_write(self.root / safe, archive.read("files/" + rel))
                    written += 1
                for rel in meta.get("created_files") or []:
                    safe = _safe_rel(rel)
                    if safe and classify(safe) is not None:
                        try:
                            (self.root / safe).unlink()
                            removed += 1
                        except OSError:
                            pass
            return {"ok": True, "written": written, "removed": removed, "restart": True}

    # --------------------------------------------------------------- export
    def export_snapshot(self, snapshot_id: str, dest: str | os.PathLike[str], passphrase: str = "") -> Dict[str, Any]:
        """A self-contained, unencrypted zip of one snapshot (files at their data-dir paths)."""
        with self._exclusive("export"):
            target = self.target()
            name = self._find_manifest(target, snapshot_id)
            header, body = self._read_manifest(target, name, passphrase)
            keys = self._keys_for(header.get("key_id", ""), target, passphrase) if header.get("encrypted") else None
            selected = self._select(body["files"], None, None)
            work = Path(tempfile.mkdtemp(prefix="export-", dir=str(self._tmp_dir())))
            try:
                blobs = self._fetch_blobs(target, selected, keys, work)
                dest = Path(dest)
                tmp = dest.with_name(dest.name + ".partial")
                with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("memuai-backup.json", json.dumps({
                        "format": EXPORT_FORMAT, "version": FORMAT_VERSION, "header": header,
                        "files": {rel: {k: v for k, v in e.items() if k != "pack"} for rel, e in selected.items()}},
                        ensure_ascii=False, indent=1))
                    for rel, entry in selected.items():
                        archive.write(blobs[entry["sha"]], "data/" + rel)
                os.replace(tmp, dest)
                return {"ok": True, "path": str(dest), "files": len(selected), "bytes": dest.stat().st_size}
            finally:
                shutil.rmtree(work, ignore_errors=True)

    def inspect_export(self, path: str | os.PathLike[str]) -> Dict[str, Any]:
        files, header = self._export_meta(Path(path))
        cats: Dict[str, Dict[str, int]] = {}
        for entry in files.values():
            row = cats.setdefault(entry.get("cat") or "other", {"files": 0, "bytes": 0, "changed": 0})
            row["files"] += 1
            row["bytes"] += int(entry.get("size") or 0)
        return {"header": header, "categories": cats}

    def _export_meta(self, path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        try:
            with zipfile.ZipFile(path) as archive:
                meta = json.loads(archive.read("memuai-backup.json"))
        except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
            raise SyncError("Это не файл резервной копии MemuAI", "This is not a MemuAI backup file", code="bad_manifest") from exc
        if meta.get("format") != EXPORT_FORMAT or not isinstance(meta.get("files"), dict):
            raise SyncError("Это не файл резервной копии MemuAI", "This is not a MemuAI backup file", code="bad_manifest")
        return meta["files"], meta.get("header") or {}

    def restore_export(self, path: str | os.PathLike[str], categories: Iterable[str] | None = None, *,
                       mirror: bool = False, profiles: Iterable[str] | None = None) -> Dict[str, Any]:
        path = Path(path)
        files, header = self._export_meta(path)
        with self._exclusive("restore"):
            selected = self._select({rel: {**e, "pack": "export"} for rel, e in files.items()}, categories, profiles)
            work = Path(tempfile.mkdtemp(prefix="import-", dir=str(self._tmp_dir())))
            try:
                blobs: Dict[str, Path] = {}
                with zipfile.ZipFile(path) as archive:
                    for rel, entry in selected.items():
                        out = work / entry["sha"]
                        if entry["sha"] in blobs:
                            continue
                        info = archive.getinfo("data/" + rel)
                        if info.file_size > 4 * 1024 * 1024 * 1024:
                            raise SyncError("Файл в архиве слишком большой", "A file in the archive is too large")
                        data = archive.read(info)
                        if hashlib.sha256(data).hexdigest() != entry["sha"]:
                            raise SyncError("Файл копии повреждён (контрольная сумма)", "A backup file is damaged (checksum)",
                                            code="corrupt")
                        out.write_bytes(data)
                        blobs[entry["sha"]] = out
                return self._apply(selected, blobs, categories, mirror=mirror, profiles=profiles,
                                   source={"file": path.name, "created": header.get("created"), "device": header.get("device")})
            finally:
                shutil.rmtree(work, ignore_errors=True)

    # ------------------------------------------------------------- status
    def status(self) -> Dict[str, Any]:
        settings = self.public_settings()
        state = self.state()
        # Round 30: one Google account for Sync, the Drive picker and Integrations. An account signed in from
        # Integrations for Gmail/Calendar only has no backup scopes yet: Sync shows «Connect» (an incremental
        # consent that keeps the other services) instead of a «connected» Drive whose every backup fails.
        connected = self.auth.connected() and self.auth.has_backup_scopes()
        cid, _, source = self.auth.client()
        return {
            "settings": settings,
            "connected": connected,
            "client_source": source,
            "bundled_client": bool(_drive_module().BUNDLED_CLIENT_ID or _bundled_google_client()),
            "account": state.get("account") if connected else None,
            "auth_flow": self.auth.flow_status(),
            "running": self.busy(),
            "last_success": state.get("last_success"),
            "last_attempt": state.get("last_attempt"),
            "last_error": state.get("last_error"),
            "last_result": state.get("last_result"),
            "last_restore": state.get("last_restore"),
            "history": (state.get("history") or [])[:HISTORY_KEEP],
            "next_due": self.next_due(settings, state),
            "paused": self._paused(),
            "device": self.device(),
            "secret_scheme": self.store.scheme,
            "proxy_scheduler": self._heartbeat_alive("proxy"),
            "presets": list(INTERVAL_PRESETS),
            "categories": [{"id": c[0], "ru": c[1], "en": c[2], "desc_ru": c[3], "desc_en": c[4], "default": c[5]}
                           for c in CATEGORIES],
        }

    def ready(self, settings: Dict[str, Any] | None = None) -> bool:
        settings = settings or self.settings()
        if settings["target"] == "folder":
            return bool(settings["folder_path"])
        return self.auth.connected() and self.auth.has_backup_scopes() and bool(self.auth.client()[0])

    def _paused(self) -> str:
        if self.paused_reason:
            try:
                return self.paused_reason() or ""
            except Exception:
                return ""
        return ""

    def next_due(self, settings: Dict[str, Any] | None = None, state: Dict[str, Any] | None = None) -> Optional[float]:
        settings = settings or self.settings()
        state = state if state is not None else self.state()
        if not settings["enabled"] or not self.ready(settings):
            return None
        interval = settings["interval_minutes"] * 60
        last = float(state.get("last_success") or 0)
        due = last + interval if last else time.time() + 120
        if settings["at_time"] and settings["interval_minutes"] >= 1440 and last:
            hh, mm = (int(x) for x in settings["at_time"].split(":"))
            base = datetime.fromtimestamp(due - 12 * 3600)
            candidate = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if candidate.timestamp() < base.timestamp():
                candidate = datetime.fromtimestamp(candidate.timestamp() + 86400)
            due = candidate.timestamp()
        failures = int(state.get("failures") or 0)
        if failures and state.get("last_attempt"):
            backoff = min(6 * 3600, 300 * 2 ** (failures - 1), interval)
            due = max(due if last else 0, float(state["last_attempt"]) + backoff)
        return due

    # ---------------------------------------------------------- scheduler
    def _heartbeat_alive(self, role: str) -> bool:
        data = self._json(f"heartbeat-{role}.json", {})
        return isinstance(data, dict) and time.time() - float(data.get("ts") or 0) < 90 and _pid_alive(int(data.get("pid") or 0))

    def request(self, reason: str) -> None:
        """Ask whichever process runs the scheduler to back up soon (used on exit)."""
        self._write_json("request.json", {"reason": reason, "ts": time.time(), "from": self.role})

    def tick(self) -> Optional[Dict[str, Any]]:
        """One scheduler step; returns the backup result when one ran."""
        self._write_json(f"heartbeat-{self.role}.json", {"pid": os.getpid(), "ts": time.time()})
        settings = self.settings()
        if self._paused():
            return None
        request = self._json("request.json", None)
        if isinstance(request, dict) and request.get("ts"):
            try:
                (self.dir / "request.json").unlink()
            except OSError:
                pass
            if time.time() - float(request["ts"]) < 6 * 3600 and self.ready(settings):
                return self._try_backup(str(request.get("reason") or "exit"))
        due = self.next_due(settings)
        if due is not None and time.time() >= due:
            return self._try_backup("auto")
        return None

    def _try_backup(self, reason: str) -> Optional[Dict[str, Any]]:
        try:
            return self.backup(reason)
        except SyncError as exc:
            if exc.code != "busy":
                log.warning("sync: %s backup failed: %s", reason, exc.en)
        except Exception as exc:  # noqa: BLE001 - recorded in state by backup()
            log.warning("sync: %s backup failed: %s", reason, type(exc).__name__)
        return None

    def start_scheduler(self, *, first_delay: float = 90.0, period: float = 20.0) -> None:
        if self._scheduler and self._scheduler.is_alive():
            return
        self._stop.clear()

        def run() -> None:
            if self._stop.wait(first_delay):
                return
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception:
                    log.exception("sync scheduler tick failed")
                if self._stop.wait(period):
                    return
        self._scheduler = threading.Thread(target=run, name=f"memuai-sync-{self.role}", daemon=True)
        self._scheduler.start()

    def stop_scheduler(self) -> None:
        self._stop.set()
        if self._run_lock.locked() and self.progress.get("reason") == "auto":
            self.cancel_event.set()
        try:
            (self.dir / f"heartbeat-{self.role}.json").unlink()
        except OSError:
            pass

    def on_exit(self, *, quick: bool = False) -> str:
        """Called when MemuAI quits.  Returns what happened: none | handed | queued | done | timeout | error.

        ``quick`` (the installer asked MemuAI to close): never block — only leave a request that the
        Proxy or the next MemuAI start picks up.
        """
        try:
            settings = self.settings()
            if not settings["enabled"] or not settings["on_exit"] or not self.ready(settings) or self._paused():
                return "none"
            if quick:
                self.request("exit")
                return "queued"
            if self._heartbeat_alive("proxy"):
                self.request("exit")
                return "handed"
            outcome = {"value": "timeout"}

            def work() -> None:
                try:
                    self.backup("exit")
                    outcome["value"] = "done"
                except Exception:
                    outcome["value"] = "error"
            thread = threading.Thread(target=work, name="memuai-sync-exit", daemon=True)
            thread.start()
            thread.join(settings["exit_timeout_s"])
            if thread.is_alive():
                self.cancel_event.set()
                thread.join(5)
            return outcome["value"]
        except Exception:
            return "error"


# ================================================================ helpers

def _manifest_name(device_id: str, snap_id: str) -> str:
    return f"memuai-manifest-{device_id}-{snap_id}.json"


def _manifest_ids(remote: Dict[str, Any], device_id: str) -> List[str]:
    prefix = f"memuai-manifest-{device_id}-"
    return [n[len(prefix):-5] for n in remote if n.startswith(prefix) and n.endswith(".json")]


def _id_ts(snap_id: str) -> float:
    try:
        v = snap_id[:16]
        return datetime(int(v[0:4]), int(v[4:6]), int(v[6:8]), int(v[9:11]), int(v[11:13]), int(v[13:15]),
                        tzinfo=timezone.utc).timestamp()
    except (ValueError, IndexError):
        return 0.0


def _files_digest(files: Dict[str, Dict[str, Any]], settings: Dict[str, Any], key_id: str) -> str:
    h = hashlib.sha256()
    h.update(json.dumps([key_id, settings["include_secrets"]]).encode())
    for rel in sorted(files):
        h.update(f"{rel}\0{files[rel]['sha']}\n".encode("utf-8", "surrogatepass"))
    return h.hexdigest()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}-{time.time_ns()}")
    try:
        from .storage import _json_transaction
        guard = _json_transaction(path) if path.suffix.lower() == ".json" else None
    except Exception:
        guard = None
    try:
        if guard is not None:
            guard.__enter__()
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(8):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 7:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        if guard is not None:
            guard.__exit__(None, None, None)
        try:
            os.remove(tmp)
        except OSError:
            pass


def _drive_module():
    from . import gdrive_sync_drive
    return gdrive_sync_drive


def _bundled_google_client() -> str:
    """Round 30: a Google client baked into the build (``MEMUAI_GOOGLE_CLIENT_ID``) — see ``app/oauth_config.py``."""
    from .oauth_config import client
    return client("google")[0]


_SERVICE: Optional[SyncService] = None
_SERVICE_LOCK = threading.Lock()


def service(role: str = "memuai") -> SyncService:
    """The process-wide service (role «memuai» in MemuAI, «proxy» in MemuAI Proxy)."""
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            opener = None
            if role == "memuai":
                import webbrowser
                opener = webbrowser.open
            _SERVICE = SyncService(role=role, open_browser=opener)
        return _SERVICE


def start_proxy_scheduler() -> Optional[SyncService]:
    """Hook for MemuAI Proxy (``app/main.py``): backups keep running while only the tray icon is left."""
    try:
        svc = service("proxy")
        # r29c: automatic backups pause while MemuAI runs in safe mode — in the Proxy too (it has no safe mode of
        # its own; the MemuAI window may be in safe mode while the tray keeps running the schedule).
        if svc.paused_reason is None:
            def _paused() -> str:
                try:
                    from . import safe_mode
                    return "safe_mode" if safe_mode.session_active() else ""
                except Exception:
                    return ""
            svc.paused_reason = _paused
        # The Proxy starts with Windows: give the PC a minute before the first check.
        svc.start_scheduler(first_delay=60.0)
        return svc
    except Exception:
        log.exception("sync: the Proxy scheduler did not start")
        return None
