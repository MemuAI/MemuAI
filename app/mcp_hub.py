"""MCP hub of the whole MemuAI (Round 40, zone r40-mcp).

``app/mcp_client.py`` keeps the protocol and the shared lazy pool of servers (stdio, Streamable HTTP, legacy
SSE). This module adds what the rest of MemuAI needs on top of it:

* a catalogue of popular servers installed in one click (real npx/uvx packages and remote endpoints);
* secrets of ``env``/headers in a protected store (DPAPI on Windows — ``gdrive_sync_crypto.SecretStore``); the
  config keeps only ``${secret:mcp/<server>/<KEY>}`` references, nothing secret reaches the UI, logs or mcp.json;
* permissions: read-only servers, «ask before calling» (never / writes / always) and per-tool allow/confirm/deny;
* scopes: Work/MemuCode see every enabled server; the main chat only those with ``chat``; a мемес (agent) those
  that list its id in ``agents``; Quick Access those with ``quick``;
* «Check the connection» (tools + resources), cached tool lists for prompts (no server starts just to build a
  prompt), ``@mcp:server/resource`` mentions, the ``mcp_call`` Planning step and the Quick Ask tool.

Routes: ``app/mcp_hub_api.py``. Window: ``pgstatic/mcp-hub.js``.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import mcp_client
from .config import DATA_DIR
from .storage import load_json_with_recovery, save_json

SECRET_DIR = Path(DATA_DIR) / "memuai" / "mcp-secrets"
TOOLS_CACHE = Path(DATA_DIR) / "memuai" / "mcp-tools-cache.json"
SECRET_HINT = re.compile(r"(?i)(token|secret|password|passwd|pass\b|_pass|api[_-]?key|apikey|auth|private|credential|dsn|uri)")
NAME_RE = re.compile(r"[^\w-]+")

CATALOG: List[Dict[str, Any]] = [
    {"id": "postgres", "title": "PostgreSQL", "cat": "db", "color": "#336791",
     "ru": "Схема, SQL-запросы, планы и здоровье базы (crystaldba/postgres-mcp). В режиме «только чтение» — restricted.",
     "en": "Schema, SQL queries, query plans and database health (crystaldba/postgres-mcp). Read-only uses restricted mode.",
     "type": "stdio", "command": "uvx", "args": ["postgres-mcp", "--access-mode={access}"],
     "access": {"read_only": "restricted", "write": "unrestricted"},
     "fields": [{"key": "DATABASE_URI", "target": "env", "secret": True, "required": True,
                 "placeholder": "postgresql://user:password@localhost:5432/db", "ru": "Строка подключения", "en": "Connection string"}],
     "docs": "https://github.com/crystaldba/postgres-mcp"},
    {"id": "mysql", "title": "MySQL", "cat": "db", "color": "#00758f",
     "ru": "Таблицы и SQL-запросы к MySQL/MariaDB (designcomputer/mysql_mcp_server).",
     "en": "Tables and SQL queries for MySQL/MariaDB (designcomputer/mysql_mcp_server).",
     "type": "stdio", "command": "uvx", "args": ["--from", "mysql-mcp-server", "mysql_mcp_server"],
     "fields": [{"key": "MYSQL_HOST", "target": "env", "default": "localhost", "ru": "Хост", "en": "Host"},
                {"key": "MYSQL_PORT", "target": "env", "default": "3306", "ru": "Порт", "en": "Port"},
                {"key": "MYSQL_USER", "target": "env", "required": True, "ru": "Пользователь", "en": "User"},
                {"key": "MYSQL_PASSWORD", "target": "env", "secret": True, "ru": "Пароль", "en": "Password"},
                {"key": "MYSQL_DATABASE", "target": "env", "required": True, "ru": "База данных", "en": "Database"}],
     "docs": "https://github.com/designcomputer/mysql_mcp_server"},
    {"id": "clickhouse", "title": "ClickHouse", "cat": "db", "color": "#d9a400",
     "ru": "Базы, таблицы и SELECT-запросы (официальный ClickHouse/mcp-clickhouse; запись выключена по умолчанию).",
     "en": "Databases, tables and SELECT queries (official ClickHouse/mcp-clickhouse; writes are off by default).",
     "type": "stdio", "command": "uvx", "args": ["mcp-clickhouse"],
     "read_only_env": {"CLICKHOUSE_ALLOW_WRITE_ACCESS": "false"},
     "fields": [{"key": "CLICKHOUSE_HOST", "target": "env", "required": True, "placeholder": "localhost", "ru": "Хост", "en": "Host"},
                {"key": "CLICKHOUSE_PORT", "target": "env", "default": "8443", "ru": "Порт", "en": "Port"},
                {"key": "CLICKHOUSE_USER", "target": "env", "default": "default", "ru": "Пользователь", "en": "User"},
                {"key": "CLICKHOUSE_PASSWORD", "target": "env", "secret": True, "ru": "Пароль", "en": "Password"},
                {"key": "CLICKHOUSE_SECURE", "target": "env", "default": "true", "ru": "TLS (true/false)", "en": "TLS (true/false)"}],
     "docs": "https://github.com/ClickHouse/mcp-clickhouse"},
    {"id": "sqlite", "title": "SQLite", "cat": "db", "color": "#0f80cc",
     "ru": "Файл базы SQLite: таблицы, запросы, заметки-инсайты.", "en": "A SQLite database file: tables, queries, insight memos.",
     "type": "stdio", "command": "uvx", "args": ["mcp-server-sqlite", "--db-path", "{db_path}"],
     "fields": [{"key": "db_path", "target": "arg", "required": True, "placeholder": "C:\\data\\app.db", "ru": "Путь к файлу .db", "en": "Path to the .db file"}],
     "docs": "https://pypi.org/project/mcp-server-sqlite/"},
    {"id": "jira", "title": "Jira / Confluence", "cat": "work", "color": "#2684ff",
     "ru": "Задачи Jira и страницы Confluence (sooperset/mcp-atlassian).", "en": "Jira issues and Confluence pages (sooperset/mcp-atlassian).",
     "type": "stdio", "command": "uvx", "args": ["mcp-atlassian"], "read_only_env": {"READ_ONLY_MODE": "true"},
     "fields": [{"key": "JIRA_URL", "target": "env", "required": True, "placeholder": "https://your-company.atlassian.net", "ru": "Адрес Jira", "en": "Jira URL"},
                {"key": "JIRA_USERNAME", "target": "env", "required": True, "placeholder": "you@company.com", "ru": "Почта", "en": "Email"},
                {"key": "JIRA_API_TOKEN", "target": "env", "secret": True, "required": True, "ru": "API-токен", "en": "API token"}],
     "docs": "https://github.com/sooperset/mcp-atlassian"},
    {"id": "linear", "title": "Linear", "cat": "work", "color": "#5e6ad2",
     "ru": "Задачи, проекты и комментарии Linear (официальный удалённый сервер, ключ API).",
     "en": "Linear issues, projects and comments (the official remote server, an API key).",
     "type": "http", "url": "https://mcp.linear.app/mcp", "headers": {"Authorization": "Bearer {LINEAR_API_KEY}"},
     "fields": [{"key": "LINEAR_API_KEY", "target": "header", "secret": True, "required": True, "placeholder": "lin_api_…",
                 "ru": "Ключ API (Settings → Security & access)", "en": "API key (Settings → Security & access)"}],
     "docs": "https://linear.app/docs/mcp"},
    {"id": "gitlab", "title": "GitLab", "cat": "dev", "color": "#fc6d26",
     "ru": "Проекты, merge request, issues, пайплайны (zereight/gitlab-mcp).", "en": "Projects, merge requests, issues, pipelines (zereight/gitlab-mcp).",
     "type": "stdio", "command": "npx", "args": ["-y", "@zereight/mcp-gitlab"], "read_only_env": {"GITLAB_READ_ONLY_MODE": "true"},
     "fields": [{"key": "GITLAB_PERSONAL_ACCESS_TOKEN", "target": "env", "secret": True, "required": True, "placeholder": "glpat-…", "ru": "Personal Access Token", "en": "Personal Access Token"},
                {"key": "GITLAB_API_URL", "target": "env", "default": "https://gitlab.com/api/v4", "ru": "Адрес API", "en": "API URL"}],
     "docs": "https://github.com/zereight/gitlab-mcp"},
    {"id": "github", "title": "GitHub", "cat": "dev", "color": "#24292f",
     "ru": "Официальный удалённый сервер GitHub: репозитории, issues, PR, Actions.", "en": "GitHub's official remote server: repositories, issues, PRs, Actions.",
     "type": "http", "url": "https://api.githubcopilot.com/mcp/", "headers": {"Authorization": "Bearer {GITHUB_TOKEN}"},
     "read_only_headers": {"X-MCP-Readonly": "true"},
     "fields": [{"key": "GITHUB_TOKEN", "target": "header", "secret": True, "required": True, "placeholder": "github_pat_…", "ru": "Personal Access Token", "en": "Personal Access Token"}],
     "docs": "https://github.com/github/github-mcp-server"},
    {"id": "git", "title": "Git", "cat": "dev", "color": "#f05032",
     "ru": "Локальный репозиторий: статус, diff, история, коммиты.", "en": "A local repository: status, diff, history, commits.",
     "type": "stdio", "command": "uvx", "args": ["mcp-server-git", "--repository", "{repository}"],
     "fields": [{"key": "repository", "target": "arg", "required": True, "placeholder": "C:\\code\\project", "ru": "Папка репозитория", "en": "Repository folder"}],
     "docs": "https://pypi.org/project/mcp-server-git/"},
    {"id": "obsidian", "title": "Obsidian", "cat": "notes", "color": "#7c3aed",
     "ru": "Заметки хранилища через плагин Local REST API (MarkusPfundstein/mcp-obsidian).",
     "en": "Vault notes through the Local REST API plugin (MarkusPfundstein/mcp-obsidian).",
     "type": "stdio", "command": "uvx", "args": ["mcp-obsidian"],
     "fields": [{"key": "OBSIDIAN_API_KEY", "target": "env", "secret": True, "required": True, "ru": "Ключ плагина Local REST API", "en": "Local REST API plugin key"},
                {"key": "OBSIDIAN_HOST", "target": "env", "default": "127.0.0.1", "ru": "Хост", "en": "Host"},
                {"key": "OBSIDIAN_PORT", "target": "env", "default": "27124", "ru": "Порт", "en": "Port"}],
     "docs": "https://github.com/MarkusPfundstein/mcp-obsidian"},
    {"id": "logseq", "title": "Logseq", "cat": "notes", "color": "#002b36",
     "ru": "Страницы и блоки Logseq через его локальный HTTP API (ergut/mcp-logseq).",
     "en": "Logseq pages and blocks through its local HTTP API (ergut/mcp-logseq).",
     "type": "stdio", "command": "uvx", "args": ["mcp-logseq"],
     "fields": [{"key": "LOGSEQ_API_TOKEN", "target": "env", "secret": True, "required": True, "ru": "Токен API-сервера Logseq", "en": "Logseq API server token"},
                {"key": "LOGSEQ_API_URL", "target": "env", "default": "http://localhost:12315", "ru": "Адрес API", "en": "API URL"}],
     "docs": "https://github.com/ergut/mcp-logseq"},
    {"id": "notion", "title": "Notion", "cat": "notes", "color": "#111111",
     "ru": "Официальный сервер Notion: поиск, страницы, базы.", "en": "Notion's official server: search, pages, databases.",
     "type": "stdio", "command": "npx", "args": ["-y", "@notionhq/notion-mcp-server"],
     "fields": [{"key": "NOTION_TOKEN", "target": "env", "secret": True, "required": True, "placeholder": "ntn_…", "ru": "Токен интеграции", "en": "Integration token"}],
     "docs": "https://github.com/makenotion/notion-mcp-server"},
    {"id": "homeassistant", "title": "Home Assistant", "cat": "home", "color": "#18bcf2",
     "ru": "Устройства и сценарии умного дома (встроенная интеграция «Model Context Protocol Server»).",
     "en": "Smart-home devices and scripts (the built-in «Model Context Protocol Server» integration).",
     "type": "http", "url": "{HA_URL}/api/mcp", "headers": {"Authorization": "Bearer {HA_TOKEN}"},
     "fields": [{"key": "HA_URL", "target": "url", "required": True, "default": "http://homeassistant.local:8123", "ru": "Адрес Home Assistant", "en": "Home Assistant URL"},
                {"key": "HA_TOKEN", "target": "header", "secret": True, "required": True, "ru": "Долгосрочный токен доступа", "en": "Long-lived access token"}],
     "docs": "https://www.home-assistant.io/integrations/mcp_server/"},
    {"id": "filesystem", "title": "Filesystem", "cat": "pc", "color": "#475569",
     "ru": "Чтение, поиск и правка файлов в разрешённой папке.", "en": "Read, search and edit files in an allowed folder.",
     "type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "{folder}"],
     "fields": [{"key": "folder", "target": "arg", "required": True, "placeholder": "C:\\Users\\me\\Documents", "ru": "Разрешённая папка", "en": "Allowed folder"}],
     "docs": "https://www.npmjs.com/package/@modelcontextprotocol/server-filesystem"},
    {"id": "fetch", "title": "Fetch", "cat": "web", "color": "#0ea5e9",
     "ru": "Скачать страницу и превратить её в Markdown.", "en": "Fetch a page and turn it into Markdown.",
     "type": "stdio", "command": "uvx", "args": ["mcp-server-fetch"], "fields": [],
     "docs": "https://pypi.org/project/mcp-server-fetch/"},
    {"id": "playwright", "title": "Playwright", "cat": "web", "color": "#2ead33",
     "ru": "Управление браузером: страницы, клики, формы, снимки (Microsoft).", "en": "Browser automation: pages, clicks, forms, snapshots (Microsoft).",
     "type": "stdio", "command": "npx", "args": ["-y", "@playwright/mcp@latest"], "fields": [],
     "docs": "https://github.com/microsoft/playwright-mcp"},
    {"id": "brave", "title": "Brave Search", "cat": "web", "color": "#fb542b",
     "ru": "Веб-поиск Brave (нужен ключ API).", "en": "Brave web search (needs an API key).",
     "type": "stdio", "command": "npx", "args": ["-y", "@brave/brave-search-mcp-server"],
     "fields": [{"key": "BRAVE_API_KEY", "target": "env", "secret": True, "required": True, "ru": "Ключ API", "en": "API key"}],
     "docs": "https://github.com/brave/brave-search-mcp-server"},
    {"id": "memory", "title": "Memory (graph)", "cat": "pc", "color": "#a855f7",
     "ru": "Граф знаний: сущности, связи, наблюдения.", "en": "A knowledge graph: entities, relations, observations.",
     "type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"], "fields": [],
     "docs": "https://www.npmjs.com/package/@modelcontextprotocol/server-memory"},
    {"id": "time", "title": "Time", "cat": "pc", "color": "#64748b",
     "ru": "Текущее время и перевод между часовыми поясами.", "en": "Current time and time-zone conversion.",
     "type": "stdio", "command": "uvx", "args": ["mcp-server-time"], "fields": [],
     "docs": "https://pypi.org/project/mcp-server-time/"},
]
CATS = {"db": ("Базы данных", "Databases"), "work": ("Задачи", "Work tracking"), "dev": ("Разработка", "Development"),
        "notes": ("Заметки", "Notes"), "home": ("Умный дом", "Smart home"), "web": ("Веб", "Web"), "pc": ("Компьютер", "Computer")}

def catalog_public() -> List[Dict[str, Any]]:
    import shutil
    rows = []
    for item in CATALOG:
        runner = item.get("command") or ""
        rows.append({k: item[k] for k in ("id", "title", "cat", "color", "ru", "en", "type", "docs") if k in item}
                    | {"fields": item.get("fields", []), "runner": runner,
                       "runner_missing": bool(runner) and shutil.which(runner) is None,
                       "has_read_only": bool(item.get("access") or item.get("read_only_env") or item.get("read_only_headers"))})
    return rows

def catalog_item(item_id: str) -> Optional[Dict[str, Any]]:
    return next((row for row in CATALOG if row["id"] == item_id), None)

_STORE: Any = None

def store() -> Any:
    global _STORE
    if _STORE is None:
        from .gdrive_sync_crypto import SecretStore
        _STORE = SecretStore(SECRET_DIR)
    return _STORE

def secret_name(server: str, key: str) -> str:
    return f"mcp/{server}/{key}"

def secret_ref(server: str, key: str) -> str:
    return "${secret:" + secret_name(server, key) + "}"

def secret_value(name: str) -> str:
    try:
        return store().get(name)
    except Exception:
        return ""

def is_secret_key(key: str) -> bool:
    return bool(SECRET_HINT.search(str(key)))

def _protect_map(server: str, values: Dict[str, str], secret_keys: List[str]) -> Tuple[Dict[str, str], List[str]]:
    """Plain values of secret-looking keys go to the store; the map keeps a reference."""
    out: Dict[str, str] = {}
    keys = list(secret_keys)
    for key, value in values.items():
        value = str(value)
        if "${secret:" in value or not value or not (key in secret_keys or is_secret_key(key)):
            out[key] = value
            continue
        store().set(secret_name(server, key), value)
        out[key] = secret_ref(server, key)
        if key not in keys:
            keys.append(key)
    return out, keys

def _header_secret(server: str, header: str, template: str, key: str, value: str) -> str:
    store().set(secret_name(server, key), value)
    return template.replace("{" + key + "}", secret_ref(server, key))

def drop_secrets(server: str, keys: List[str]) -> None:
    for key in keys:
        try:
            store().delete(secret_name(server, key))
        except Exception:
            pass

def clean_name(raw: str) -> str:
    return NAME_RE.sub("", str(raw or "").strip().replace(" ", "-"))[:40]

class HubError(ValueError):
    def __init__(self, ru: str, en: str = "") -> None:
        super().__init__(ru)
        self.ru, self.en = ru, en or ru

    def text(self, lang: str = "ru") -> str:
        return self.en if lang == "en" else self.ru

def _raw_servers() -> Dict[str, Any]:
    data = load_json_with_recovery(mcp_client.CONFIG_PATH, {"servers": {}}, validator=lambda x: isinstance(x, dict))
    servers = data.get("servers") if isinstance(data, dict) else {}
    return dict(servers) if isinstance(servers, dict) else {}

def _write_servers(servers: Dict[str, Any]) -> None:
    mcp_client.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    save_json(mcp_client.CONFIG_PATH, {"servers": servers}, backup=True)

def install(item_id: str, values: Dict[str, Any], *, name: str = "", read_only: bool = True,
            chat: bool = False) -> str:
    item = catalog_item(item_id)
    if item is None:
        raise HubError("Такого сервера нет в каталоге", "No such server in the catalogue")
    servers = _raw_servers()
    base = clean_name(name) or item["id"]
    final, n = base, 2
    while final in servers:
        final, n = f"{base}-{n}", n + 1
    values = {str(k): str(v).strip() for k, v in (values or {}).items()}
    missing = [f for f in item.get("fields", []) if f.get("required") and not (values.get(f["key"]) or f.get("default"))]
    if missing:
        f = missing[0]
        raise HubError(f"Заполните поле «{f['ru']}»", f"Fill in “{f['en']}”")
    filled = {f["key"]: values.get(f["key"]) or str(f.get("default") or "") for f in item.get("fields", [])}
    access = (item.get("access") or {}).get("read_only" if read_only else "write", "")
    args = [a.replace("{access}", access) for a in item.get("args", [])]
    args = [re.sub(r"\{(\w+)\}", lambda m: filled.get(m.group(1), m.group(0)), a) for a in args]
    env: Dict[str, str] = {}
    headers: Dict[str, str] = {}
    secret_keys: List[str] = []
    url = item.get("url", "")
    for f in item.get("fields", []):
        key, value = f["key"], filled.get(f["key"], "")
        if f["target"] == "env" and value:
            if f.get("secret"):
                store().set(secret_name(final, key), value)
                env[key] = secret_ref(final, key)
                secret_keys.append(key)
            else:
                env[key] = value
        elif f["target"] == "url":
            url = url.replace("{" + key + "}", value.rstrip("/"))
    for header, template in (item.get("headers") or {}).items():
        for f in item.get("fields", []):
            if f["target"] == "header" and "{" + f["key"] + "}" in template:
                template = _header_secret(final, header, template, f["key"], filled.get(f["key"], ""))
                secret_keys.append(f["key"])
        headers[header] = template
    if read_only:
        env.update(item.get("read_only_env") or {})
        headers.update(item.get("read_only_headers") or {})
    row: Dict[str, Any] = {"type": item["type"], "enabled": True, "description": item["en"][:300], "catalog_id": item["id"],
                           "read_only": bool(read_only), "confirm": "write", "chat": bool(chat), "secret_keys": secret_keys}
    if item["type"] == "stdio":
        row.update({"command": item["command"], "args": args, "env": env})
    else:
        row.update({"url": url, "headers": headers})
    servers[final] = row
    _write_servers(servers)
    return final

def add_custom(body: Dict[str, Any]) -> str:
    """A server described by hand: a command line (stdio) or a URL (http / sse)."""
    import shlex
    servers = _raw_servers()
    name = clean_name(body.get("name"))
    if not name:
        raise HubError("Укажите имя сервера", "Name the server")
    if name in servers:
        raise HubError("Сервер с таким именем уже есть", "A server with this name exists")
    kind = str(body.get("type") or "stdio")
    row: Dict[str, Any] = {"type": kind if kind in mcp_client.TRANSPORTS else "stdio", "enabled": True,
                           "description": str(body.get("description") or "")[:300], "confirm": "write"}
    env = {str(k): str(v) for k, v in (body.get("env") or {}).items() if str(k)} if isinstance(body.get("env"), dict) else {}
    if row["type"] == "stdio":
        raw = str(body.get("command") or "").strip()
        try:
            parts = shlex.split(raw, posix=True)
        except ValueError:
            parts = raw.split()
        if not parts:
            raise HubError("Укажите команду запуска", "Give the start command")
        row.update({"command": parts[0], "args": parts[1:]})
    else:
        url = str(body.get("url") or "").strip()
        if not re.match(r"^https?://", url):
            raise HubError("Укажите адрес http(s)://", "Give an http(s):// URL")
        row["url"] = url
        headers = body.get("headers") if isinstance(body.get("headers"), dict) else {}
        token = str(body.get("token") or "").strip()
        if token:
            store().set(secret_name(name, "TOKEN"), token)
            headers = {**headers, "Authorization": "Bearer " + secret_ref(name, "TOKEN")}
            row["secret_keys"] = ["TOKEN"]
        row["headers"] = headers
    env, keys = _protect_map(name, env, [])
    row["env"] = env
    row["secret_keys"] = sorted(set(row.get("secret_keys", []) + keys))
    servers[name] = row
    _write_servers(servers)
    return name

def update(name: str, patch: Dict[str, Any]) -> None:
    servers = _raw_servers()
    row = servers.get(name)
    if not isinstance(row, dict):
        raise HubError("Сервер не найден", "Server not found")
    for key in ("enabled", "read_only", "chat", "quick"):
        if key in patch:
            row[key] = patch[key] is True
    if patch.get("confirm") in ("never", "write", "always"):
        row["confirm"] = patch["confirm"]
    if isinstance(patch.get("agents"), list):
        row["agents"] = [str(a)[:80] for a in patch["agents"] if str(a)][:50]
    if isinstance(patch.get("tool_rules"), dict):
        rules = dict(row.get("tool_rules") or {})
        for tool, rule in patch["tool_rules"].items():
            if rule in ("allow", "confirm", "deny"):
                rules[str(tool)[:120]] = rule
            else:
                rules.pop(str(tool), None)
        row["tool_rules"] = rules
    if "timeout" in patch:
        row["timeout"] = max(5, min(int(patch.get("timeout") or 120), 900))
    if "description" in patch:
        row["description"] = str(patch["description"] or "")[:300]
    if isinstance(patch.get("env"), dict):
        env = dict(row.get("env") or {})
        keys = list(row.get("secret_keys") or [])
        for key, value in patch["env"].items():
            key = str(key).strip()
            if not key:
                continue
            if value is None:
                env.pop(key, None)
                if key in keys:
                    drop_secrets(name, [key])
                    keys.remove(key)
            elif str(value) != "":
                if key in keys or is_secret_key(key):
                    store().set(secret_name(name, key), str(value))
                    env[key] = secret_ref(name, key)
                    if key not in keys:
                        keys.append(key)
                else:
                    env[key] = str(value)
        row["env"], row["secret_keys"] = env, keys
    servers[name] = row
    _write_servers(servers)

def remove(name: str) -> None:
    servers = _raw_servers()
    row = servers.pop(name, None)
    if isinstance(row, dict):
        drop_secrets(name, list(row.get("secret_keys") or []))
    _write_servers(servers)
    cache = _tools_cache()
    if cache.pop(name, None) is not None:
        save_json(TOOLS_CACHE, cache)

def migrate_plain_secrets() -> int:
    """Tokens a user once typed into the old MemuCode panel / mcp.json move into the protected store."""
    servers = _raw_servers()
    moved = 0
    for name, row in servers.items():
        if not isinstance(row, dict) or not isinstance(row.get("env"), dict):
            continue
        before = dict(row["env"])
        row["env"], row["secret_keys"] = _protect_map(name, before, list(row.get("secret_keys") or []))
        moved += sum(1 for k in before if before[k] != row["env"][k])
    if moved:
        _write_servers(servers)
    return moved

_READ_PREFIX = re.compile(r"(?i)^(get|list|read|search|find|fetch|describe|show|count|lookup|query|explain|analy[sz]e|"
                          r"browse|view|stat|info|directory_tree|check|inspect|resolve|preview|summar)")
_WRITE_WORD = re.compile(r"(?i)(delete|drop|remove|destroy|truncate|write|create|update|insert|upsert|execute|exec\b|"
                         r"run|send|push|merge|move|rename|edit|set_|patch|post|publish|close|reopen|assign|"
                         r"transition|commit|reset|kill|call_service|turn_on|turn_off|toggle|control)")

def is_read_only_tool(tool: Dict[str, Any]) -> bool:
    notes = tool.get("annotations") or {}
    if notes.get("readOnlyHint") is True:
        return True
    if notes.get("destructiveHint") is True or notes.get("readOnlyHint") is False:
        return False
    name = str(tool.get("name") or "")
    return bool(_READ_PREFIX.match(name)) and not _WRITE_WORD.search(name)

def policy(spec: Dict[str, Any], tool: Dict[str, Any]) -> str:
    """allow | confirm | deny for one tool of one server."""
    name = str(tool.get("name") or "")
    rule = (spec.get("tool_rules") or {}).get(name)
    read_only = is_read_only_tool(tool)
    if rule == "deny":
        return "deny"
    if spec.get("read_only") and not read_only and rule != "allow":
        return "deny"
    if rule in ("allow", "confirm"):
        return rule
    mode = spec.get("confirm") or "write"
    if mode == "always":
        return "confirm"
    if mode == "never":
        return "allow"
    return "allow" if read_only else "confirm"

def _tools_cache() -> Dict[str, Any]:
    data = load_json_with_recovery(TOOLS_CACHE, {}, validator=lambda x: isinstance(x, dict))
    return data if isinstance(data, dict) else {}

def remember_tools(name: str, server: Any) -> None:
    cache = _tools_cache()
    cache[name] = {"tools": server.tools, "resources": server.resources[:200], "at": time.time(),
                   "server_info": getattr(server, "server_info", {})}
    save_json(TOOLS_CACHE, cache)

def known(name: str) -> Dict[str, Any]:
    """Tools/resources of a server: the live ones, otherwise the last successful connection."""
    live = mcp_client.registry.live(name)
    if live is not None and live.running and live.tools:
        return {"tools": live.tools, "resources": live.resources, "live": True}
    row = _tools_cache().get(name) or {}
    return {"tools": row.get("tools") or [], "resources": row.get("resources") or [], "live": False, "at": row.get("at", 0)}

def in_scope(spec: Dict[str, Any], scope: str, agent: str = "") -> bool:
    if not spec.get("enabled"):
        return False
    if scope in ("work", "code", "flow"):
        return True
    if scope == "quick":
        return bool(spec.get("quick"))
    if agent and agent in (spec.get("agents") or []):
        return True
    return bool(spec.get("chat"))

def scoped(scope: str, agent: str = "") -> List[Dict[str, Any]]:
    """Servers of a scope with their known tools and each tool's policy (for prompts and the window)."""
    rows = []
    for name, spec in mcp_client.load_config()["servers"].items():
        if not in_scope(spec, scope, agent):
            continue
        info = known(name)
        tools = []
        for tool in info["tools"]:
            rule = policy(spec, tool)
            if rule != "deny":
                tools.append({"name": tool["name"], "description": tool.get("description", "")[:240], "policy": rule,
                              "read_only": is_read_only_tool(tool)})
        rows.append({"name": name, "read_only": spec.get("read_only", False), "tools": tools,
                     "resources": [{"uri": r["uri"], "name": r.get("name", "")} for r in info["resources"][:50]],
                     "description": spec.get("description", "")})
    return rows

def split_tool_name(full: str) -> Tuple[str, str]:
    """``mcp_<server>_<tool>`` → (server, tool); the server part is matched against the config (names hold «_»)."""
    rest = full[4:] if full.lower().startswith("mcp_") else full
    servers = mcp_client.load_config()["servers"]
    lowered = rest.lower()
    server = next((n for n in sorted(servers, key=len, reverse=True)
                   if lowered == n.lower() or lowered.startswith(n.lower() + "_")), "")
    return server, rest[len(server):].lstrip("_") if server else ""

def _tool_spec(server: str, tool: str) -> Dict[str, Any]:
    return next((t for t in known(server)["tools"] if t.get("name") == tool), {"name": tool})

def tr(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru

async def call(server: str, tool: str, args: Dict[str, Any], *, scope: str = "work", agent: str = "",
               confirmed: bool = False, full_access: bool = False, lang: str = "ru") -> Dict[str, Any]:
    """One tool call with the scope and the permissions applied.

    ``{"needs_confirm": True, …}`` when the user has to allow it first (the window asks, then repeats with
    ``confirmed``); ``{"error": …}`` for refused / failed calls; otherwise ``{"content", "is_error"}``."""
    spec = mcp_client.load_config()["servers"].get(server)
    if spec is None:
        return {"error": tr(lang, f"Сервер MCP «{server}» не настроен", f"MCP server “{server}” is not configured")}
    if not in_scope(spec, scope, agent):
        return {"error": tr(lang, f"Сервер MCP «{server}» не подключён к этому чату (Настройки → Интеграции → MCP)",
                            f"MCP server “{server}” is not connected to this chat (Settings → Integrations → MCP)")}
    tool_row = _tool_spec(server, tool)
    rule = policy(spec, tool_row)
    if rule == "confirm" and full_access and spec.get("confirm") == "write" and tool not in (spec.get("tool_rules") or {}):
        rule = "allow"
    if rule == "deny":
        return {"error": tr(lang, f"Инструмент «{tool}» запрещён настройками сервера «{server}»"
                            + (" (только чтение)" if spec.get("read_only") else ""),
                            f"Tool “{tool}” is not allowed by the settings of “{server}”"
                            + (" (read-only)" if spec.get("read_only") else ""))}
    if rule == "confirm" and not confirmed:
        return {"needs_confirm": True, "server": server, "tool": tool, "read_only": spec.get("read_only", False),
                "args_preview": mcp_client.redact(json.dumps(args, ensure_ascii=False)[:800], spec)}
    try:
        server_obj = await mcp_client.registry.server(server)
        remember_tools(server, server_obj)
        value = await server_obj.call(tool, args or {})
    except Exception as exc:
        return {"error": f"MCP «{server}»: " + mcp_client.redact(mcp_client.error_text(exc, lang), spec)[:400]}
    return value

async def check(name: str, lang: str = "ru") -> Dict[str, Any]:
    """«Check the connection»: (re)start, list tools and resources, remember them."""
    spec = mcp_client.load_config()["servers"].get(name)
    if spec is None:
        return {"ok": False, "error": tr(lang, "Сервер не найден", "Server not found")}
    started = time.time()
    await mcp_client.registry.stop(name)
    try:
        if not spec.get("enabled"):
            raise mcp_client.MCPError("Сервер выключен", "The server is turned off")
        server = await mcp_client.registry.server(name)
    except Exception as exc:
        live = mcp_client.registry.live(name)
        return {"ok": False, "error": mcp_client.redact(mcp_client.error_text(exc, lang), spec)[:400],
                "logs": list(live.logs)[-30:] if live else [], "ms": int((time.time() - started) * 1000)}
    remember_tools(name, server)
    return {"ok": True, "ms": int((time.time() - started) * 1000), "server_info": server.server_info,
            "tools": [{**t, "read_only": is_read_only_tool(t), "policy": policy(spec, t)} for t in server.tools],
            "resources": server.resources[:200], "logs": list(server.logs)[-30:]}

def public_rows(lang: str = "ru") -> List[Dict[str, Any]]:
    """Servers for Settings → Integrations → MCP — never a secret value."""
    rows = []
    for name, spec in mcp_client.load_config()["servers"].items():
        base = mcp_client.registry.row(name, spec)
        info = known(name)
        live = mcp_client.registry.live(name)
        env = []
        for key, value in spec.get("env", {}).items():
            secret = key in spec.get("secret_keys", []) or "${secret:" in value or is_secret_key(key)
            env.append({"key": key, "secret": secret, "value": "" if secret else value,
                        "set": bool(mcp_client.resolve_secrets({key: value})[key]) if secret else bool(value)})
        rows.append({**base, "headers": sorted(spec.get("headers", {}).keys()), "env": env,
                     "tools": [{"name": t["name"], "description": t.get("description", ""), "read_only": is_read_only_tool(t),
                                "policy": policy(spec, t), "rule": (spec.get("tool_rules") or {}).get(t["name"], "")}
                               for t in info["tools"]],
                     "resources": info["resources"][:100], "cached": not info.get("live"),
                     "read_only": spec.get("read_only"), "confirm": spec.get("confirm"), "chat": spec.get("chat"),
                     "quick": spec.get("quick"), "agents": spec.get("agents"), "catalog_id": spec.get("catalog_id"),
                     "timeout": spec.get("timeout"), "logs": list(live.logs)[-40:] if live else []})
    return rows

def _on_loop(coro: Any, timeout: float) -> Any:
    """Mention providers run in a worker thread; servers live on the app's event loop."""
    loop = mcp_client.registry.loop
    if loop is None or loop.is_closed():
        coro.close()
        raise RuntimeError("MCP is not started yet")
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

def mention_search(query: str, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """``@mcp:`` → servers; ``@mcp:server/`` → its resources (cached list; the server is not started)."""
    from .mentions import fuzzy
    q = str(query or "").strip()
    server_q, _, res_q = q.partition("/")
    rows: List[Dict[str, Any]] = []
    lang = "en" if str(ctx.get("lang") or "") == "en" else "ru"
    for name, spec in mcp_client.load_config()["servers"].items():
        if not spec.get("enabled") or (server_q and "/" not in q and fuzzy(server_q, name) < 40 and server_q not in name):
            continue
        if "/" in q and name.lower() != server_q.lower():
            continue
        for res in known(name)["resources"]:
            label = res.get("name") or res["uri"]
            if res_q and fuzzy(res_q, label, res["uri"]) < 40:
                continue
            rows.append({"title": f"{name}/{label}"[:160], "subtitle": res["uri"][:200], "ref": {"server": name, "uri": res["uri"]},
                         "icon": "panel"})
        if not known(name)["resources"] and "/" not in q:
            rows.append({"title": name, "subtitle": tr(lang, "нет ресурсов — нажмите «Проверить соединение» в настройках MCP",
                                                        "no resources — press «Check the connection» in MCP settings"),
                         "ref": None, "disabled": True, "icon": "panel"})
    return rows[:30]

def mention_resolve(ref: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(ref, str) and "/" in ref:
        server, _, uri = ref.partition("/")
        ref = {"server": server, "uri": uri}
    if not isinstance(ref, dict) or not ref.get("server") or not ref.get("uri"):
        raise ValueError("bad MCP resource reference")
    server, uri = str(ref["server"]), str(ref["uri"])
    spec = mcp_client.load_config()["servers"].get(server)
    if spec is None or not spec.get("enabled"):
        raise ValueError(f"MCP server {server!r} is not available")
    res = _on_loop(mcp_client.registry.read_resource(server, uri), 60)
    return {"title": f"{server}: {uri}"[:200], "source": f"MCP · {server}", "text": res.get("text") or ""}

def register_mentions() -> None:
    from . import mentions
    mentions.register_provider(mentions.Provider(
        id="mcp", label=("MCP", "MCP"), aliases=("mcp", "мсп"), icon="panel",
        hint=("ресурс сервера MCP: @mcp:сервер/ресурс", "an MCP server resource: @mcp:server/resource"),
        search=mention_search, resolve=mention_resolve, remote=True, order=80, global_search=False,
        available=lambda: bool(mcp_client.load_config()["servers"])))

def quick_description(_settings: Any = None) -> str:
    rows = scoped("quick")
    lines = []
    for row in rows:
        for tool in row["tools"][:25]:
            if tool["policy"] == "allow":
                lines.append(f"{row['name']}.{tool['name']}" + (f" ({tool['description'][:90]})" if tool["description"] else ""))
    if not lines:
        return ""
    return ("{server, tool, args} — a tool of a connected MCP server (only these: " + "; ".join(lines[:40]) + ")")

async def quick_handler(args: Dict[str, Any], ctx: Any = None) -> Dict[str, Any]:
    server, tool = str(args.get("server") or ""), str(args.get("tool") or "")
    lang = "en" if isinstance(ctx, dict) and ctx.get("lang") == "en" else "ru"
    result = await call(server, tool, args.get("args") if isinstance(args.get("args"), dict) else {},
                        scope="quick", lang=lang)
    if result.get("needs_confirm"):
        return {"error": tr(lang, "Этот инструмент требует подтверждения — выполните его из основного чата MemuAI.",
                            "This tool needs approval — run it from the main MemuAI chat.")}
    return result

def register_quick() -> None:
    try:
        from . import quick_ask_api
    except Exception:
        return
    if "mcp" not in quick_ask_api.EXTRA_TOOLS:
        quick_ask_api.register_tool("mcp", quick_description, quick_handler,
                                    label=lambda a, lang: f"MCP · {a.get('server', '')}.{a.get('tool', '')}",
                                    enabled=lambda _s: bool(quick_description()))
