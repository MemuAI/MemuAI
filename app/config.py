"""Глобальные настройки MemuAI Proxy + надёжное хранение."""
from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import secrets
import threading
import urllib.parse
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List

from . import search_engines

def _default_data_dir() -> str:
    env = os.environ.get("MEMUAI_DATA_DIR") or os.environ.get("OMNIHUB_DATA_DIR")
    if env:
        return env
    if os.name == "nt":
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(appdata, "MemuAI")
    # Linux/macOS: XDG Base Directory, ``~/.local/share`` when XDG_DATA_HOME
    # is unset or relative (the spec requires an absolute path).
    xdg = os.environ.get("XDG_DATA_HOME") or ""
    if not xdg or not os.path.isabs(xdg):
        xdg = os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(xdg, "MemuAI")

def default_data_dir(product: str = "MemuAI") -> str:
    """Data dir for ``product`` (``"MemuAI"`` or ``"MemuAIProxy"``).

    Same rules as :func:`_default_data_dir`: an explicit
    ``MEMUAI_DATA_DIR``/``OMNIHUB_DATA_DIR`` wins, otherwise ``%APPDATA%``
    on Windows and the XDG data dir elsewhere. Entry points that still
    compute the path by hand (``main.py``, ``proxy_only.py``) should
    switch to this helper so the suite and the standalone Proxy agree
    on where their data lives.
    """
    if os.environ.get("MEMUAI_DATA_DIR") or os.environ.get("OMNIHUB_DATA_DIR"):
        return _default_data_dir()
    if product == "MemuAIProxy":
        return os.path.join(os.path.dirname(_default_data_dir()), "MemuAIProxy")
    return _default_data_dir()

DATA_DIR = _default_data_dir()
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

RETIRED_PROVIDER_IDS = frozenset({"opencode"})

BALANCING_MODES = ("round-robin", "order", "first-available", "least-used")
UPDATE_INTERVAL_HOURS = (6, 12, 24, 168)
UPDATE_CHANNELS = ("stable", "preview")
SEARCH_ENGINES = search_engines.engine_ids()
CONTEXT_MODES = ("auto", "server", "stateless")
REASONING_OUTPUTS = ("reasoning_content", "think_tags", "hidden")
CHAT_BACKENDS = ("agent", "copilot", "auto")

# F13: OWASP now recommends 600k PBKDF2-HMAC-SHA256 iterations (was 60k);
# salts are 16 bytes (was 8). Old 60k hashes still verify once and are then
# upgraded to 600k on the next successful login (see check_admin_password).
_PBKDF2_ITERATIONS = 600_000
_PBKDF2_LEGACY_ITERATIONS = 60_000
# Readable first-run password alphabet: no 0/O/1/l/I to mistype.
_FIRST_RUN_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def hash_password(password: str, salt: str | None = None, *,
                  iterations: int = _PBKDF2_ITERATIONS) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations).hex()
    return digest, salt


def _password_matches(password: str, salt: str, expected: str) -> tuple[bool, bool]:
    """Check a password against current and legacy iteration counts.

    Returns ``(matches, is_legacy)`` — legacy matches are upgraded by the
    caller instead of being accepted silently forever.
    """
    for iterations, legacy in ((_PBKDF2_ITERATIONS, False), (_PBKDF2_LEGACY_ITERATIONS, True)):
        digest, _ = hash_password(password, salt, iterations=iterations)
        if secrets.compare_digest(digest, expected):
            return True, legacy
    return False, False


def generate_first_run_password() -> str:
    """A random, typable first-run password (4 groups of 4 characters)."""
    alphabet = _FIRST_RUN_PASSWORD_ALPHABET
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4))

@dataclass
class Settings:

    host: str = "127.0.0.1"
    port: int = 8317
    open_ui_on_start: bool = True
    close_to_tray: bool = True

    admin_password_hash: str = ""
    admin_password_salt: str = ""

    api_keys: List[str] = field(default_factory=list)
    # F15: CORS used to be on by default with `Access-Control-Allow-Origin: *`.
    # Now off by default; when enabled, only the explicit allow-list below
    # is reflected — never "*".
    cors_enabled: bool = False
    cors_origins: List[str] = field(default_factory=list)

    proxy_enabled: bool = True

    use_prefixes: bool = True
    default_provider: str = ""
    strict_models: bool = False
    balancing: str = "round-robin"
    model_refresh_on_start: bool = True
    model_refresh_interval: int = 360

    default_proxy: str = ""
    verify_ssl: bool = True
    request_timeout: int = 180
    stream_idle_timeout: int = 300

    first_byte_timeout: int = 90

    max_retry_passes: int = 2
    retry_delay_ms: int = 1500

    sse_keepalive: bool = True
    sse_keepalive_interval: int = 15

    provider_settings: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    search_engine: str = "auto"
    search_api_key: str = ""
    search_base_url: str = ""
    search_results: int = 5

    search_keys: Dict[str, str] = field(default_factory=dict)
    search_urls: Dict[str, str] = field(default_factory=dict)

    search_schema: int = 0

    log_requests: bool = True
    log_persist: bool = True
    log_payloads: bool = True
    metrics_enabled: bool = True

    model_hidden_keywords: List[str] = field(default_factory=list)
    cookie_check_interval: int = 30
    model_seed_version: int = 0
    imported_from: str = ""

    companion_model: str = ""

    # Автообновления отдельного Proxy: настройки живут здесь, а не в
    # state.json обновлений, чтобы их было видно в обычной панели.
    # Дефолты — как у MemuMusic: проверка и автоскачивание включены,
    # раз в день, только стабильные версии.
    updates_enabled: bool = True
    updates_auto_download: bool = True
    updates_interval_hours: int = 24
    updates_channel: str = "stable"

def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off", ""):
            return False
        return default
    return value if isinstance(value, bool) else default

def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(minimum, min(maximum, parsed))

_HOSTNAME_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z"
)

def _normalized_bind_host(value: Any, default: str | None = "127.0.0.1") -> str | None:
    if not isinstance(value, str):
        return default
    host = value.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        if _HOSTNAME_RE.fullmatch(host):
            return host
    return default

def valid_cors_origin(value: Any) -> bool:
    """One explicit `scheme://host[:port]` origin — no wildcards, no paths."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    if "*" in value:
        return False
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        return False
    return True


def _is_loopback_host(value: str) -> bool:
    host = str(value or "").strip().strip("[]")
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False

def _valid_settings_document(value: Any) -> bool:
    """Reject shapes that would silently remove authentication/network state."""
    if not isinstance(value, dict):
        return False
    if "api_keys" in value and not isinstance(value["api_keys"], (list, tuple)):
        return False
    if "provider_settings" in value and not isinstance(value["provider_settings"], dict):
        return False
    if "host" in value and _normalized_bind_host(value["host"], None) is None:
        return False
    if "port" in value:
        try:
            if isinstance(value["port"], bool) or not 1 <= int(value["port"]) <= 65535:
                return False
        except (TypeError, ValueError, OverflowError):
            return False
    password_parts = (value.get("admin_password_hash"), value.get("admin_password_salt"))
    if any(password_parts) and not _valid_password_material(*password_parts):
        return False
    return True

def _valid_password_material(digest: Any, salt: Any) -> bool:
    return (isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
            and isinstance(salt, str) and 1 <= len(salt) <= 256)

def _normalized_settings(raw: Any) -> Dict[str, Any]:
    defaults = asdict(Settings())
    source = raw if isinstance(raw, dict) else {}
    result: Dict[str, Any] = {}
    for key, default in defaults.items():
        value = source.get(key, default)
        if isinstance(default, bool):
            result[key] = _as_bool(value, default)
        elif isinstance(default, int):
            result[key] = _bounded_int(value, default, -2_147_483_648, 2_147_483_647)
        elif isinstance(default, str):
            result[key] = str(value or "")[:4096]
        else:
            result[key] = value
    result["port"] = _bounded_int(source.get("port"), defaults["port"], 1, 65535)
    result["request_timeout"] = _bounded_int(source.get("request_timeout"), defaults["request_timeout"], 5, 3600)
    result["stream_idle_timeout"] = _bounded_int(source.get("stream_idle_timeout"), defaults["stream_idle_timeout"], 5, 3600)
    result["cookie_check_interval"] = _bounded_int(source.get("cookie_check_interval"), defaults["cookie_check_interval"], 0, 10080)
    result["model_refresh_interval"] = _bounded_int(source.get("model_refresh_interval"), defaults["model_refresh_interval"], 0, 10080)
    result["updates_interval_hours"] = source.get("updates_interval_hours") \
        if source.get("updates_interval_hours") in UPDATE_INTERVAL_HOURS else defaults["updates_interval_hours"]
    result["updates_channel"] = source.get("updates_channel") \
        if source.get("updates_channel") in UPDATE_CHANNELS else defaults["updates_channel"]
    result["model_seed_version"] = _bounded_int(source.get("model_seed_version"), defaults["model_seed_version"], 0, 1_000_000)
    result["first_byte_timeout"] = _bounded_int(source.get("first_byte_timeout"), defaults["first_byte_timeout"], 5, 3600)
    result["max_retry_passes"] = _bounded_int(source.get("max_retry_passes"), defaults["max_retry_passes"], 1, 10)
    result["retry_delay_ms"] = _bounded_int(source.get("retry_delay_ms"), defaults["retry_delay_ms"], 0, 60_000)
    result["sse_keepalive_interval"] = _bounded_int(source.get("sse_keepalive_interval"), defaults["sse_keepalive_interval"], 3, 120)
    result["search_results"] = _bounded_int(source.get("search_results"), defaults["search_results"], 1, 10)
    engine = str(source.get("search_engine", "") or "").strip().lower()
    legacy = _bounded_int(source.get("search_schema"), 0, 0, 100) < 2
    if legacy and engine == "duckduckgo":
        engine = "auto"
    result["search_engine"] = engine if engine in SEARCH_ENGINES else defaults["search_engine"]
    result["search_schema"] = 2
    for field_name, limit in (("search_keys", 512), ("search_urls", 2000)):
        raw_map = source.get(field_name) if isinstance(source.get(field_name), dict) else {}
        result[field_name] = {str(name): str(value).strip()[:limit] for name, value in raw_map.items()
                              if str(name) in SEARCH_ENGINES and str(value).strip()}
    if legacy:

        if result["search_api_key"].strip():
            result["search_keys"].setdefault("tavily", result["search_api_key"].strip()[:512])
        if result["search_base_url"].strip():
            result["search_urls"].setdefault("searxng", result["search_base_url"].strip()[:2000])
    raw_keywords = source.get("model_hidden_keywords")
    if not isinstance(raw_keywords, (list, tuple)):
        raw_keywords = []
    result["model_hidden_keywords"] = list(dict.fromkeys(
        str(item).strip()[:80] for item in raw_keywords if str(item).strip()))[:64]
    result["host"] = _normalized_bind_host(source.get("host"), defaults["host"])
    result["balancing"] = source.get("balancing") if source.get("balancing") in BALANCING_MODES else defaults["balancing"]
    keys = source.get("api_keys") if isinstance(source.get("api_keys"), (list, tuple)) else []
    result["api_keys"] = list(dict.fromkeys(str(key).strip()[:512] for key in keys if str(key).strip()))[:64]
    provider_settings = source.get("provider_settings") if isinstance(source.get("provider_settings"), dict) else {}
    result["provider_settings"] = {
        str(provider)[:80]: dict(settings)
        for provider, settings in list(provider_settings.items())[:100]
        if isinstance(settings, dict) and str(provider)
    }
    if result.get("default_provider") in RETIRED_PROVIDER_IDS:
        result["default_provider"] = ""
    return result

class ConfigStore:
    """Потокобезопасное чтение/запись config.json."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runtime_host: str | None = None
        self._runtime_port: int | None = None
        # F13: the generated first-run password, in memory only — never
        # persisted and returned exactly once via take_first_run_password().
        self.first_run_password_value: str | None = None
        self.settings = Settings()
        self.load()

    def load(self) -> None:
        from .storage import load_json_with_recovery
        with self._lock:
            missing = object()
            repair_malformed = False
            raw = load_json_with_recovery(
                CONFIG_PATH, missing, validator=_valid_settings_document)
            if raw is missing:

                malformed = load_json_with_recovery(CONFIG_PATH, missing)
                if isinstance(malformed, dict):
                    raw = malformed
                    repair_malformed = True
            if raw is missing:
                self.settings = Settings()
            else:
                self.settings = Settings(**_normalized_settings(raw))
            if raw is missing or not _valid_password_material(
                    self.settings.admin_password_hash, self.settings.admin_password_salt):
                # F13: never fall back to the well-known "admin" password.
                # Generate a random one; the desktop launcher shows it once
                # (main.py) and it is never written to disk in plain text.
                first_run = generate_first_run_password()
                digest, salt = hash_password(first_run)
                self.settings.admin_password_hash = digest
                self.settings.admin_password_salt = salt
                self.first_run_password_value = first_run
                repair_malformed = True
            if repair_malformed:
                self.save()

    def save(self) -> None:
        from .storage import save_json
        with self._lock:
            save_json(CONFIG_PATH, asdict(self.settings))

    @property
    def effective_host(self) -> str:
        return getattr(self, "_runtime_host", None) or self.settings.host

    @property
    def effective_port(self) -> int:
        return getattr(self, "_runtime_port", None) or self.settings.port

    def set_runtime_endpoint(self, host: str, port: int) -> None:
        """Remember the actual bind without changing persisted preferences."""
        normalized_host = _normalized_bind_host(host, None)
        if normalized_host is None or not 1 <= int(port) <= 65535:
            raise ValueError("invalid runtime endpoint")
        with self._lock:
            self._runtime_host = normalized_host
            self._runtime_port = int(port)

    def take_first_run_password(self) -> str | None:
        """The generated first-run password, returned once, then forgotten."""
        with self._lock:
            value = self.first_run_password_value
            self.first_run_password_value = None
            return value

    @property
    def first_run_password(self) -> bool:
        """True while the admin password is still the known default "admin".

        Checks both iteration counts so a legacy 60k default hash is still
        recognised (and LAN binding stays blocked for it).
        """
        with self._lock:
            ok, _legacy = _password_matches(
                "admin", self.settings.admin_password_salt, self.settings.admin_password_hash)
            return ok

    def check_admin_password(self, password: str) -> bool:
        with self._lock:
            ok, legacy = _password_matches(
                password, self.settings.admin_password_salt, self.settings.admin_password_hash)
            if ok and legacy:
                # F13: upgrade a legacy 60k hash to 600k on successful login.
                digest, salt = hash_password(password)
                self.settings.admin_password_hash = digest
                self.settings.admin_password_salt = salt
                self.save()
            return ok

    def set_admin_password(self, password: str) -> None:
        digest, salt = hash_password(password)
        with self._lock:
            self.settings.admin_password_hash = digest
            self.settings.admin_password_salt = salt
            self.save()

    def check_api_key(self, key: str | None) -> bool:
        if not self.settings.api_keys:

            return _is_loopback_host(self.effective_host)
        if not key:
            return False

        given = str(key).encode("utf-8")
        return any(secrets.compare_digest(given, str(candidate).encode("utf-8"))
                   for candidate in self.settings.api_keys)

    def authorize_api_request(self, key: str | None, *, host: str = "",
                              origin: str = "", fetch_site: str = "", peer: str = "") -> bool:
        """Keep keyless loopback clients working without granting access to websites.

        The app's own window never has the hub key to send (the UI sends no auth
        header), so a configured key must not lock it out: a request that arrived
        on the loopback interface, for a loopback Host, with a same-authority
        Origin (or none) and a non-cross-site fetch is the window itself. A website
        cannot fake that combination — the browser sets Origin and Sec-Fetch-Site —
        and a remote caller cannot fake the peer address, so the Host/Origin pair
        alone is never trusted. Callers that do not pass ``peer`` (the Proxy's /v1)
        keep the old behaviour: the key is required whenever one is set.
        """
        if self._is_own_window(host, origin, fetch_site, peer):
            return True
        if not self.check_api_key(key):
            return False
        if self.settings.api_keys:
            return True
        if fetch_site.strip().lower() == "cross-site":
            return False
        try:
            host_name = (urllib.parse.urlsplit("//" + host).hostname or "").lower()
        except ValueError:
            return False
        if fetch_site.strip() and host_name not in ("127.0.0.1", "localhost", "::1"):

            return False
        if not origin:
            return True
        try:
            parsed_origin = urllib.parse.urlsplit(origin)
            same_authority = parsed_origin.netloc.lower() == host.lower()
        except ValueError:
            return False
        return host_name in ("127.0.0.1", "localhost", "::1") and same_authority

    @staticmethod
    def _is_own_window(host: str, origin: str, fetch_site: str, peer: str) -> bool:
        """True for a request from the app's own window (see authorize_api_request)."""
        if not peer:
            return False
        # "testclient" is Starlette's test client (same idiom as telegram_use_api).
        if peer != "testclient" and not _is_loopback_host(peer):
            return False
        if fetch_site.strip().lower() == "cross-site":
            return False
        try:
            host_name = (urllib.parse.urlsplit("//" + host).hostname or "").lower()
        except ValueError:
            return False
        if host_name not in ("127.0.0.1", "localhost", "::1"):
            return False
        if not origin:
            return True
        try:
            parsed_origin = urllib.parse.urlsplit(origin)
        except ValueError:
            return False
        return parsed_origin.netloc.lower() == host.lower()

    def provider_cfg(self, provider_id: str) -> Dict[str, Any]:
        return self.settings.provider_settings.setdefault(provider_id, {})

    def public_dict(self) -> Dict[str, Any]:
        with self._lock:
            d = asdict(self.settings)
            d.pop("admin_password_hash", None)
            d.pop("admin_password_salt", None)
            d["admin_password_is_default"] = self.first_run_password
            return d

    def apply_updates(self, updates: Dict[str, Any]) -> List[str]:
        """Применяет скалярные изменения; provider_settings мерджится по ключам."""
        if not isinstance(updates, dict):
            return ["settings"]
        fields = {f.name for f in Settings.__dataclass_fields__.values()}
        rejected: List[str] = []
        changed = False
        with self._lock:
            candidate = asdict(self.settings)
            for key, value in updates.items():
                if key not in fields or key.startswith("admin_password"):
                    rejected.append(str(key))
                    continue
                current = candidate[key]
                try:
                    if key == "provider_settings":
                        if not isinstance(value, dict):
                            raise TypeError
                        merged = dict(current)
                        for pid, cfg in value.items():
                            if not isinstance(pid, str) or not pid or not isinstance(cfg, dict):
                                rejected.append(f"provider_settings.{pid}")
                                continue
                            merged.setdefault(pid, {}).update(cfg)
                        candidate[key] = merged
                    elif key in ("search_keys", "search_urls"):

                        if not isinstance(value, dict) or any(
                                not isinstance(name, str) or not isinstance(item, str)
                                for name, item in value.items()):
                            raise TypeError
                        merged = dict(current)
                        for name, item in value.items():
                            if name not in SEARCH_ENGINES:
                                raise ValueError
                            if item.strip():
                                merged[name] = item.strip()
                            else:
                                merged.pop(name, None)
                        candidate[key] = merged
                    elif key == "model_hidden_keywords":
                        if (not isinstance(value, (list, tuple))
                                or any(not isinstance(item, str) for item in value)):
                            raise TypeError
                        candidate[key] = [item.strip() for item in value if item.strip()]
                    elif key == "api_keys":
                        if (not isinstance(value, (list, tuple))
                                or any(not isinstance(item, str) for item in value)):
                            raise TypeError
                        candidate[key] = list(value)
                    elif key == "cors_origins":
                        # F15: only valid explicit origins are stored; wildcards
                        # and malformed values are silently dropped, never saved.
                        if (not isinstance(value, (list, tuple))
                                or any(not isinstance(item, str) for item in value)):
                            raise TypeError
                        candidate[key] = [item.strip() for item in value
                                          if item.strip() and valid_cors_origin(item.strip())]
                    elif isinstance(current, bool):
                        if isinstance(value, bool):
                            candidate[key] = value
                        elif isinstance(value, str) and value.strip().lower() in (
                                "1", "true", "yes", "on", "0", "false", "no", "off", ""):
                            candidate[key] = _as_bool(value, current)
                        else:
                            raise TypeError
                    elif isinstance(current, int) and not isinstance(current, bool):
                        if isinstance(value, bool):
                            raise TypeError
                        candidate[key] = int(value)
                    elif isinstance(current, str):
                        if not isinstance(value, str):
                            raise TypeError
                        candidate[key] = str(value).strip()
                    else:
                        candidate[key] = value
                    if key == "host" and _normalized_bind_host(candidate[key], None) is None:
                        raise ValueError
                    integer_bounds = {
                        "port": (1, 65535),
                        "first_byte_timeout": (5, 3600),
                        "max_retry_passes": (1, 10),
                        "retry_delay_ms": (0, 60_000),
                        "sse_keepalive_interval": (3, 120),
                        "request_timeout": (5, 3600),
                        "stream_idle_timeout": (5, 3600),
                        "cookie_check_interval": (0, 10080),
                        "model_refresh_interval": (0, 10080),
                        "model_seed_version": (0, 1_000_000),
                        "search_results": (1, 10),
                    }
                    if key == "search_engine" and candidate[key] not in SEARCH_ENGINES:
                        raise ValueError
                    if key == "updates_interval_hours" and candidate[key] not in UPDATE_INTERVAL_HOURS:
                        raise ValueError
                    if key == "updates_channel" and candidate[key] not in UPDATE_CHANNELS:
                        raise ValueError
                    if key in integer_bounds:
                        minimum, maximum = integer_bounds[key]
                        if not minimum <= candidate[key] <= maximum:
                            raise ValueError
                    changed = True
                except (TypeError, ValueError, OverflowError):
                    rejected.append(str(key))
                    candidate[key] = current
            if changed:
                self.settings = Settings(**_normalized_settings(candidate))
                self.save()
        return rejected

config_store = ConfigStore()
