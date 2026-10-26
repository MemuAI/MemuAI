"""Repository-backed, checksum-pinned module catalogue and reversible installs.

The catalogue is metadata, never executable content. Packages use the existing
portable module format and importer. A new or updated package stays disabled;
its persistent data, launcher and window state live outside the package.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from typing import Any, Callable
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
import uuid

import httpx

from . import modules_host as host
from .path_safety import is_link
from .storage import _json_transaction, load_json_with_recovery, save_json

DEFAULT_SOURCE = "https://raw.githubusercontent.com/MemuAI/MemuAI/main/module-catalog/index.json"
REPOSITORY = "https://github.com/MemuAI/MemuAI"
CONTRIBUTING = REPOSITORY + "/blob/main/module-catalog/CONTRIBUTING.md"
INDEX_LIMIT = 1_500_000
PACKAGE_LIMIT = 8 * 1024 * 1024
MAX_ENTRIES = 500
CACHE_SECONDS = 900
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,39}\Z")
_CATEGORY = re.compile(r"[a-z][a-z0-9-]{0,39}\Z")

class CatalogError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status

def _text(value: Any, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]

def _url(raw: Any) -> str:
    value = str(raw or "").strip()
    if len(value) > 2048:
        raise CatalogError("Адрес слишком длинный / URL is too long")
    if not value or any(ord(c) < 33 for c in value) or "\\" in value:
        raise CatalogError("Нужен полный HTTPS-адрес каталога / Enter a complete HTTPS catalogue URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise CatalogError("Некорректный адрес / Invalid URL") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname or port not in (None, 443):
        raise CatalogError("Разрешён публичный HTTPS на стандартном порту / Use public HTTPS on its standard port")
    if parsed.username is not None or parsed.password is not None or parsed.fragment or parsed.query:
        raise CatalogError("Адрес не должен содержать пароль, параметры или фрагмент / URL credentials, queries and fragments are not supported")

    if parsed.hostname.lower() == "github.com":
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 5 and parts[2] == "blob":
            return _url("https://raw.githubusercontent.com/" + "/".join(parts[:2] + parts[3:]))
    if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
        raise CatalogError("Локальные адреса не подходят для каталога / Local catalogue addresses are not allowed")
    hostname = parsed.hostname.lower()
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise CatalogError("Нужен публичный адрес каталога / A public catalogue address is required")
        if address.version == 6:
            hostname = "[" + hostname + "]"
    return urlunsplit(("https", hostname, parsed.path or "/", "", ""))

def _package_url(source: str, path: Any) -> str:
    raw = str(path or "").strip()
    if len(raw) > 1024:
        raise CatalogError("Путь пакета слишком длинный / Package path is too long")
    decoded = unquote(raw)
    if not raw or "\\" in decoded or any(part in (".", "..") for part in decoded.split("/")):
        raise CatalogError("Небезопасный путь пакета / Unsafe package path")
    result = _url(urljoin(source, raw))
    if urlsplit(result).netloc != urlsplit(source).netloc:
        raise CatalogError("Пакет должен находиться у источника каталога / A package must share the catalogue origin")
    if not urlsplit(result).path.endswith(".json"):
        raise CatalogError("Каталог принимает переносимые JSON-пакеты / The catalogue accepts portable JSON packages")
    return result

def settings(root: Path) -> dict:
    value = load_json_with_recovery(Path(root) / "catalog-settings.json", {}, validator=lambda v:isinstance(v, dict))
    try:
        source = _url(value.get("source") or DEFAULT_SOURCE)
    except CatalogError:
        source = DEFAULT_SOURCE
    return {"source":source, "default_source":DEFAULT_SOURCE, "official":source == DEFAULT_SOURCE}

def set_source(root: Path, source: str, *, guard: Callable = lambda:None) -> dict:
    value = _url(source or DEFAULT_SOURCE)
    with _json_transaction(Path(root) / "catalog-settings.json"):
        guard()
        save_json(Path(root) / "catalog-settings.json", {"source":value}, backup=True)
    return settings(root)

def normalize_index(value: Any, source: str) -> dict:
    if not isinstance(value, dict) or type(value.get("schema")) is not int or value["schema"] != 1 or not isinstance(value.get("modules"), list):
        raise CatalogError("Неизвестный формат каталога / Unsupported catalogue format")
    if len(value["modules"]) > MAX_ENTRIES:
        raise CatalogError("В каталоге слишком много записей / Too many catalogue entries")
    entries, ids = [], set()
    for row in value["modules"]:
        if not isinstance(row, dict):
            raise CatalogError("Повреждена запись каталога / Invalid catalogue entry")
        module_id = host.validate_id(row.get("id"))
        if module_id in ids:
            raise CatalogError("В каталоге повторяется ID / Duplicate catalogue ID")
        ids.add(module_id)
        version = str(row.get("version") or "").strip()
        if not _SAFE_VERSION.fullmatch(version):
            raise CatalogError("Некорректная версия пакета / Invalid package version")
        package = row.get("package") if isinstance(row.get("package"), dict) else {}
        digest = str(package.get("sha256") or "").strip().lower()
        if not _SHA.fullmatch(digest):
            raise CatalogError("Пакету нужен SHA-256 / Package SHA-256 is required")
        size = package.get("bytes")
        if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= PACKAGE_LIMIT:
            raise CatalogError("Некорректный размер пакета / Invalid package size")
        permissions = row.get("permissions")
        if (not isinstance(permissions, list) or len(permissions) > 64
                or any(not isinstance(p, str) or not re.fullmatch(r"[a-z][a-z0-9_.:-]{0,79}", p) for p in permissions)):
            raise CatalogError("В каталоге должны быть явно перечислены доступы / Catalogue permissions must be explicit")
        category = _text(row.get("category"), 40) or "productivity"
        if not _CATEGORY.fullmatch(category):
            raise CatalogError("Некорректная категория / Invalid category")
        tags = row.get("tags") if isinstance(row.get("tags"), list) else []
        minimum = str(row.get("min_app_version") or "").strip()
        if minimum and (len(minimum) > 30 or not re.fullmatch(r"\d+(?:\.\d+){0,3}", minimum)):
            raise CatalogError("Некорректная минимальная версия MemuAI / Invalid minimum MemuAI version")
        entries.append({
            "id":module_id, "version":version,
            "title":_text(row.get("title"), 100) or module_id,
            "title_en":_text(row.get("title_en"), 100),
            "summary":_text(row.get("summary"), 500), "summary_en":_text(row.get("summary_en"), 500),
            "description":_text(row.get("description"), 6000), "description_en":_text(row.get("description_en"), 6000),
            "author":_text(row.get("author"), 100), "license":_text(row.get("license"), 80),
            "category":category, "tags":list(dict.fromkeys(_text(tag, 32) for tag in tags if _text(tag, 32)))[:12],
            "icon":_text(row.get("icon"), 32) if re.fullmatch(r"[a-z-]+", str(row.get("icon") or "")) else "work",
            "permissions":list(dict.fromkeys(permissions)),
            "min_app_version":minimum,
            "homepage":_url(row["homepage"]) if row.get("homepage") else "",
            "package":{"path":_text(package.get("path"), 1024), "url":_package_url(source, package.get("path")), "sha256":digest, "bytes":size},
        })
    return {"schema":1, "name":_text(value.get("name"), 100) or "MemuAI", "name_en":_text(value.get("name_en"), 100),
            "description":_text(value.get("description"), 600), "description_en":_text(value.get("description_en"), 600),
            "modules":entries}

def _seed() -> dict:
    path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "app" / "module_catalog_seed.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

async def download(url: str, limit: int) -> bytes:

    from .character_import import safe_fetch
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=8), trust_env=False, follow_redirects=False) as client:
        try:
            data, _, final = await asyncio.wait_for(safe_fetch(client, url, limit=limit, label="Каталог модулей"), 45)
        except Exception as exc:
            raise CatalogError("Не удалось загрузить каталог или пакет / Could not download catalogue or package", 502) from exc
    if urlsplit(final).netloc != urlsplit(url).netloc:
        raise CatalogError("Источник перенаправил на другой сайт / Source redirected to another origin", 502)
    return data

def _cache(root: Path, source: str) -> dict | None:
    raw = load_json_with_recovery(Path(root) / "catalog-cache.json", {}, validator=lambda v:isinstance(v, dict))
    if raw.get("source") != source:
        return None
    try:
        index = normalize_index(raw.get("index"), source)
        checked = int(raw.get("checked") or 0)
        if checked < 0 or checked > int(time.time()) + 60 or raw.get("mode") not in {"bundled", "remote"}:
            return None
    except (ValueError, TypeError, OverflowError):
        return None
    return {**raw, "index":index, "checked":checked}

async def catalogue(root: Path, *, refresh: bool = False, fetch: Callable = download, guard: Callable = lambda:None) -> dict:
    root = Path(root)
    await asyncio.to_thread(recover_pending, root)
    source = settings(root)["source"]
    cached = await asyncio.to_thread(_cache, root, source)
    now = int(time.time())
    if cached and not refresh and now - int(cached.get("checked") or 0) < CACHE_SECONDS:
        guard()
        if settings(root)["source"] != source:
            raise CatalogError("Источник каталога сменился / Catalogue source changed", 409)
        return await asyncio.to_thread(enrich, root, cached)
    error = ""
    try:
        data = await fetch(source, INDEX_LIMIT)
        if len(data) > INDEX_LIMIT:
            raise CatalogError("Каталог превышает лимит / Catalogue exceeds the size limit")
        index = normalize_index(json.loads(data.decode("utf-8-sig")), source)
        snapshot = {"source":source, "index":index, "mode":"remote", "checked":now, "error":""}
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error = str(exc)[:400]
        if cached:
            snapshot = {**cached, "checked":now, "error":error}
        elif source == DEFAULT_SOURCE and _seed().get("index"):
            snapshot = {"source":source, "index":normalize_index(_seed()["index"], source), "mode":"bundled", "checked":now, "error":error}
        else:
            raise CatalogError("Каталог недоступен. Проверьте адрес или повторите позже / Catalogue unavailable; check its URL or try again", 502) from exc
    guard()
    if settings(root)["source"] != source:
        raise CatalogError("Источник каталога сменился / Catalogue source changed", 409)
    await asyncio.to_thread(save_json, root / "catalog-cache.json", snapshot, backup=False)
    return await asyncio.to_thread(enrich, root, snapshot)

def _folder(root: Path, module_id: str) -> Path:
    target = Path(root) / "custom" / host.validate_id(module_id)
    boundary = Path(root).resolve()
    if not target.resolve().is_relative_to(boundary) or is_link(target) or is_link(target.parent):
        raise CatalogError("Каталог модуля содержит ссылку / The module directory contains a link")
    return target

def _manifest_marker(folder: Path) -> dict:
    try:
        raw = json.loads((folder / ".catalog.json").read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}

def _fingerprint(folder: Path) -> dict:
    host._reject_package_links(folder)
    records, total = {}, 0
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name == ".catalog.json":
            continue
        stat = path.stat()
        if stat.st_size > 8 * 1024 * 1024 or total + stat.st_size > 24 * 1024 * 1024:
            raise CatalogError("Файл модуля слишком большой для обновления / Module file is too large to update")
        data = path.read_bytes()
        total += len(data)
        if total > 24 * 1024 * 1024 or len(records) >= 300:
            raise CatalogError("Пакет слишком большой для безопасного обновления / Package is too large to update safely")
        stat = path.stat()
        records[path.relative_to(folder).as_posix()] = {"sha256":hashlib.sha256(data).hexdigest(), "size":len(data), "mtime_ns":stat.st_mtime_ns}
    return records

def _modified(folder: Path, marker: dict, *, strict: bool = False) -> bool:
    expected = marker.get("files")
    if not isinstance(expected, dict) or not expected:
        return True
    host._reject_package_links(folder)
    paths = {p.relative_to(folder).as_posix():p for p in folder.rglob("*") if p.is_file() and p.name != ".catalog.json"}
    if set(paths) != set(expected):
        return True
    for name, path in paths.items():
        saved = expected[name]
        if not isinstance(saved, dict):
            return True
        stat = path.stat()
        if stat.st_size != saved.get("size"):
            return True
        if (strict or stat.st_mtime_ns != saved.get("mtime_ns")) and hashlib.sha256(path.read_bytes()).hexdigest() != saved.get("sha256"):
            return True
    return False

def enrich(root: Path, snapshot: dict) -> dict:
    config = settings(root)
    installed = {row["id"]:row for row in host.list_modules(root)}
    from .version import APP_VERSION, version_tuple
    rows = []
    for entry in snapshot["index"]["modules"]:
        row = copy.deepcopy(entry)
        own = installed.get(row["id"])
        marker = _manifest_marker(_folder(root, row["id"])) if own and not own.get("builtin") else {}
        compatible = not row["min_app_version"] or version_tuple(APP_VERSION) >= version_tuple(row["min_app_version"])
        unknown = [p for p in row["permissions"] if p not in host.PERMISSIONS]
        row.update(installed=bool(own), installed_version=own.get("version", "") if own else "", enabled=bool(own and own.get("enabled")),
                   compatible=compatible and not unknown, unsupported_permissions=unknown,
                   managed=bool(marker.get("source") == snapshot["source"]), local_modified=False, can_rollback=False)
        if own:
            if own.get("builtin") or not row["managed"]:
                row["status"] = "conflict"
            else:
                try:
                    row["local_modified"] = _modified(_folder(root, row["id"]), marker)
                except (ValueError, OSError):
                    row["local_modified"] = True
                row["status"] = "installed" if marker.get("sha256") == row["package"]["sha256"] else "update"
                row["can_rollback"] = bool(_backups(root, row["id"]))
        else:
            row["status"] = "available"
        rows.append(row)
    return {**config, "name":snapshot["index"]["name"], "name_en":snapshot["index"].get("name_en", ""),
            "mode":snapshot["mode"], "checked":snapshot["checked"], "error":snapshot.get("error", ""),
            "modules":rows, "contributing_url":CONTRIBUTING, "app_version":APP_VERSION,
            "limits":{"entries":MAX_ENTRIES, "package_bytes":PACKAGE_LIMIT}}

def validate_package(value: Any, entry: dict, *, allow_unsupported: bool = False) -> dict:
    if not isinstance(value, dict) or value.get("format") != host.PACKAGE_FORMAT or value.get("version") != host.PACKAGE_VERSION:
        raise CatalogError("Пакет должен быть экспортом MemuAI версии 2 / Expected a MemuAI portable v2 package")
    manifest, files = value.get("manifest"), value.get("files")
    if not isinstance(manifest, dict) or not isinstance(files, dict):
        raise CatalogError("В пакете нет манифеста или файлов / Package manifest or files are missing")
    if manifest.get("id") != entry["id"] or str(manifest.get("version")) != entry["version"]:
        raise CatalogError("ID или версия пакета не совпадает с каталогом / Package ID or version does not match the catalogue")
    if (not isinstance(manifest.get("permissions"), list)
            or any(not isinstance(p, str) for p in manifest["permissions"])
            or set(manifest["permissions"]) != set(entry["permissions"])):
        raise CatalogError("Доступы пакета отличаются от показанных / Package permissions differ from those shown")
    if not allow_unsupported and any(p not in host.PERMISSIONS for p in manifest["permissions"]):
        raise CatalogError("Нужна более новая версия MemuAI для этих доступов / Update MemuAI to support these permissions")
    from .module_integration import contributed_files, normalize_contributes, PACKAGE_FILE_LIMIT, PACKAGE_FILES_TOTAL
    contributions = value.get("contributes", manifest.get("contributes"))
    if "contributes" in value and "contributes" in manifest and value["contributes"] != manifest["contributes"]:
        raise CatalogError("Описания расширений пакета не совпадают / Package contribution declarations disagree")
    errors = []
    normalize_contributes(contributions, errors=errors)
    if errors:
        raise CatalogError("Некорректные расширения пакета / Invalid package contributions: " + "; ".join(errors)[:250])
    allowed = {"index.html", "style.css", "main.js", "i18n.json", *contributed_files(contributions)}
    if set(files) - allowed:
        raise CatalogError("Пакет содержит необъявленные файлы / Package contains undeclared files")
    contributed_size = 0
    for name, text in files.items():
        if name == "i18n.json":
            if not isinstance(text, dict):
                raise CatalogError("Переводы должны быть JSON-объектом / Translations must be a JSON object")
            text = json.dumps(host.normalize_i18n(text), ensure_ascii=False)
        if not isinstance(text, str) or len(text) > (host._CODE_LIMIT if name in {"index.html","style.css","main.js","i18n.json"} else PACKAGE_FILE_LIMIT):
            raise CatalogError("Файл пакета превышает лимит / Package file exceeds its limit")
        if name not in {"index.html","style.css","main.js","i18n.json"}:
            contributed_size += len(text)
    if contributed_size > PACKAGE_FILES_TOTAL:
        raise CatalogError("Слишком много дополнительных файлов / Too much contributed file data")
    if not files.get("index.html") and not files.get("main.js"):
        raise CatalogError("В пакете нет приложения / Package has no application code")
    return value

def _backups(root: Path, module_id: str) -> list[Path]:
    folder = Path(root) / "catalog-backups" / host.validate_id(module_id)
    if not folder.is_dir() or is_link(folder) or not folder.resolve().is_relative_to(Path(root).resolve()):
        return []
    return sorted((p for p in folder.iterdir() if re.fullmatch(r"[0-9]{13}-[0-9a-f]{32}", p.name) and p.is_dir() and not is_link(p)), reverse=True)

def _safe_remove(path: Path, boundary: Path) -> None:
    resolved = path.resolve()
    if resolved == boundary.resolve() or not resolved.is_relative_to(boundary.resolve()) or is_link(path):
        raise CatalogError("Небезопасный путь временного пакета / Unsafe temporary package path")
    shutil.rmtree(path)

def _pending(root: Path, module_id: str) -> Path:
    folder = Path(root) / "catalog-pending"
    if is_link(folder) or not folder.resolve().is_relative_to(Path(root).resolve()):
        raise CatalogError("Небезопасный журнал установки / Unsafe install journal")
    path = folder / (host.validate_id(module_id) + ".json")
    if is_link(path):
        raise CatalogError("Журнал установки содержит ссылку / Install journal contains a link")
    return path

def recover_pending(root: Path) -> None:
    """Recover an interrupted directory swap, without resurrecting removed modules."""
    root = Path(root)
    folder = root / "catalog-pending"
    if not folder.is_dir():
        return
    if is_link(folder):
        raise CatalogError("Небезопасный журнал установки / Unsafe install journal")
    for record in folder.glob("*.json"):
        module_id = record.stem
        try:
            live = _folder(root, module_id)
            pending = _pending(root, module_id)
        except ValueError:
            continue
        with host._package_transaction(live):
            journal = load_json_with_recovery(pending, {}, validator=lambda v:isinstance(v, dict))
            if journal.get("id") != module_id:
                continue
            name = str(journal.get("backup") or "")
            if name and not re.fullmatch(r"[0-9]{13}-[0-9a-f]{32}", name):
                continue
            backup = root / "catalog-backups" / module_id / name if name else None
            if not live.exists() and backup is not None and backup.exists():
                if is_link(backup) or not backup.resolve().is_relative_to(root.resolve()):
                    raise CatalogError("Небезопасная резервная копия / Unsafe backup")
                host._reject_package_links(backup)
                live.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup, live)
                host.configure_module(root, module_id, enabled=bool(journal.get("was_enabled")))
            elif live.exists() and _manifest_marker(live).get("sha256") == journal.get("sha256"):
                host.configure_module(root, module_id, enabled=False)
            pending.unlink(missing_ok=True)

def _commit(root: Path, entry: dict, payload: dict, source: str, *, replace_local: bool, guard: Callable) -> dict:
    root = Path(root)
    live = _folder(root, entry["id"])
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".catalog-stage-", dir=root))
    try:

        host.import_package(temporary, payload)
        staged = temporary / "custom" / entry["id"]
        marker = {"source":source, "sha256":entry["package"]["sha256"], "version":entry["version"],
                  "installed":int(time.time()), "files":_fingerprint(staged)}
        save_json(staged / ".catalog.json", marker, backup=False)
        with host._package_transaction(live):
            guard()
            if settings(root)["source"] != source:
                raise CatalogError("Источник каталога сменился / Catalogue source changed", 409)
            existing = next((row for row in host.list_modules(root) if row["id"] == entry["id"]), None)
            old_enabled = bool(existing and existing.get("enabled"))
            if existing:
                provenance = _manifest_marker(live)
                if existing.get("builtin") or provenance.get("source") != source:
                    raise CatalogError("Этот ID занят другим модулем. Он не будет заменён / This ID belongs to another module and cannot be replaced", 409)
                if _modified(live, provenance, strict=True) and not replace_local:
                    raise CatalogError("В модуле есть свои правки. Подтвердите их замену / Module has local edits; confirm replacement", 409)
            backup = None
            if existing:
                backup_root = root / "catalog-backups" / entry["id"]
                if is_link(backup_root) or not backup_root.resolve().is_relative_to(root.resolve()):
                    raise CatalogError("Небезопасная папка резервных копий / Unsafe backup directory")
                backup_root.mkdir(parents=True, exist_ok=True)
                backup = backup_root / f"{int(time.time()*1000):013}-{uuid.uuid4().hex}"
            pending = _pending(root, entry["id"])
            save_json(pending, {"id":entry["id"], "backup":backup.name if backup else "",
                                "sha256":entry["package"]["sha256"], "was_enabled":old_enabled}, backup=False)
            try:
                live.parent.mkdir(parents=True, exist_ok=True)
                if existing:
                    host.configure_module(root, entry["id"], enabled=False)
                if live.exists():
                    host._reject_package_links(live)
                    os.replace(live, backup)
                os.replace(staged, live)
                installed = host.configure_module(root, entry["id"], enabled=False)
            except BaseException:
                if backup is not None and backup.exists():
                    if live.exists():
                        _safe_remove(live, root)
                    os.replace(backup, live)
                    if existing:
                        host.configure_module(root, entry["id"], enabled=old_enabled)
                elif live.exists() and not existing:
                    _safe_remove(live, root)
                elif existing and live.exists():
                    host.configure_module(root, entry["id"], enabled=old_enabled)
                pending.unlink(missing_ok=True)
                raise
            else:
                pending.unlink(missing_ok=True)
            for old in _backups(root, entry["id"])[2:]:
                try:
                    _safe_remove(old, root)
                except OSError:
                    pass
            return {"module":installed, "preserved_data":True, "disabled":True, "backup":bool(backup)}
    finally:
        if temporary.exists():
            _safe_remove(temporary, root)

async def _verified_package(root: Path, module_id: str, digest: str, source: str, *,
                            fetch: Callable, guard: Callable, inspection: bool = False) -> tuple[dict, dict, str]:
    source = _url(source)
    module_id = host.validate_id(module_id)
    guard()
    if settings(root)["source"] != source:
        raise CatalogError("Источник каталога сменился / Catalogue source changed", 409)
    listing = await catalogue(root, fetch=fetch, guard=guard)
    entry = next((row for row in listing["modules"] if row["id"] == module_id), None)
    if not entry or entry["package"]["sha256"] != str(digest).lower():
        raise CatalogError("Запись каталога изменилась. Обновите список / Catalogue entry changed; refresh the list", 409)
    if not inspection and not entry["compatible"]:
        raise CatalogError("Модулю нужна более новая версия MemuAI / This module requires a newer MemuAI version", 409)
    raw = None
    if listing["mode"] == "bundled" and source == DEFAULT_SOURCE:
        value = _seed().get("packages", {}).get(entry["package"]["path"])
        if isinstance(value, str):
            raw = value.encode("utf-8")
    if raw is None:
        raw = await fetch(entry["package"]["url"], PACKAGE_LIMIT)
    if len(raw) != entry["package"]["bytes"] or hashlib.sha256(raw).hexdigest() != entry["package"]["sha256"]:
        raise CatalogError("Размер или SHA-256 пакета не совпадает. Ничего не установлено / Package size or SHA-256 mismatch; nothing was installed", 409)
    try:
        package_text = raw.decode("utf-8")
        payload = validate_package(json.loads(package_text[1:] if package_text.startswith("\ufeff") else package_text), entry, allow_unsupported=inspection)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CatalogError("Пакет не является JSON / Package is not valid JSON") from exc
    guard()
    if settings(root)["source"] != source:
        raise CatalogError("Источник каталога сменился / Catalogue source changed", 409)
    return entry, payload, package_text

async def preview(root: Path, module_id: str, digest: str, source: str, *,
                  fetch: Callable = download, guard: Callable = lambda:None) -> dict:
    entry, payload, package_text = await _verified_package(root, module_id, digest, source, fetch=fetch, guard=guard, inspection=True)
    files = {"manifest.json":json.dumps(payload["manifest"], ensure_ascii=False, indent=2) + "\n"}
    for name, content in payload["files"].items():
        files[name] = json.dumps(content, ensure_ascii=False, indent=2) + "\n" if isinstance(content, dict) else content
    if payload.get("contributes") and "contributes" not in payload["manifest"]:
        files["contributes.json"] = json.dumps(payload["contributes"], ensure_ascii=False, indent=2) + "\n"
    return {"id":entry["id"], "version":entry["version"], "sha256":entry["package"]["sha256"],
            "bytes":entry["package"]["bytes"], "files":files, "package_text":package_text,
            "package_name":entry["id"] + ".memu-module.json"}

async def install(root: Path, module_id: str, digest: str, source: str, *, replace_local: bool = False,
                  fetch: Callable = download, guard: Callable = lambda:None) -> dict:
    entry, payload, _ = await _verified_package(root, module_id, digest, source, fetch=fetch, guard=guard)
    source = _url(source)
    if not entry["compatible"]:
        raise CatalogError("Модулю нужна более новая версия MemuAI / This module requires a newer MemuAI version", 409)
    return await asyncio.to_thread(_commit, root, entry, payload, source, replace_local=replace_local, guard=guard)

def rollback(root: Path, module_id: str, *, replace_local: bool = False, guard: Callable = lambda:None) -> dict:
    root, module_id = Path(root), host.validate_id(module_id)
    recover_pending(root)
    live = _folder(root, module_id)
    with host._package_transaction(live):
        guard()
        backups = _backups(root, module_id)
        if not backups or not _manifest_marker(live):
            raise CatalogError("Нет предыдущей версии / No previous version is available", 404)
        if _modified(live, _manifest_marker(live), strict=True) and not replace_local:
            raise CatalogError("Есть локальные правки. Подтвердите откат / Local edits exist; confirm rollback", 409)
        previous = backups[0]
        host._reject_package_links(previous)

        try:
            package = host.export_package(root, module_id)
        except Exception:
            package = {"format": "memu-module-package", "version": 1, "manifest": {}, "files": {}, "saved": {}}
        marker = _manifest_marker(previous)
        old_manifest = json.loads((previous / "manifest.json").read_text(encoding="utf-8"))
        from .module_integration import package_files
        contributes = old_manifest.get("contributes")
        package["manifest"] = old_manifest
        package["files"] = {"index.html":(previous/"index.html").read_text(encoding="utf-8"),
                            "style.css":(previous/"style.css").read_text(encoding="utf-8"),
                            "main.js":(previous/"main.js").read_text(encoding="utf-8"),
                            "i18n.json":host._read_i18n(previous), **package_files(previous, contributes)}
        if contributes:
            package["contributes"] = contributes
        else:
            package.pop("contributes", None)
        entry = {"id":module_id,"version":str(old_manifest["version"]),"package":{"sha256":marker["sha256"]}}
        active_source = settings(root)["source"]
        return _commit(root, entry, package, active_source, replace_local=True, guard=guard)

def contribution(root: Path, module_id: str, metadata: dict) -> dict:
    payload = host.export_package(root, module_id)
    manifest = payload["manifest"]
    author = _text(metadata.get("author") or manifest.get("author"), 100)
    license_name = _text(metadata.get("license"), 80)
    if not author or not license_name:
        raise CatalogError("Укажите автора и лицензию / Enter the author and license")
    manifest["author"] = author
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    data = text.encode("utf-8")
    if len(data) > PACKAGE_LIMIT:
        raise CatalogError("Пакет превышает лимит каталога / Package exceeds the catalogue limit")
    entry = {key:manifest.get(key, "") for key in ("id","version","title","title_en","summary","summary_en","permissions")}
    entry.update(author=author, license=license_name, category=_text(metadata.get("category"),40) or "productivity",
                 tags=[_text(t,32) for t in str(metadata.get("tags") or "").split(",") if _text(t,32)][:12],
                 icon=manifest.get("icon") if re.fullmatch(r"[a-z-]+",str(manifest.get("icon") or "")) else "work",
                 package={"path":"packages/"+manifest["id"]+".memu-module.json", "bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()})
    normalize_index({"schema":1,"modules":[entry]},DEFAULT_SOURCE)
    validate_package(payload,entry)
    return {"package_text":text,"entry":entry,"package_path":"module-catalog/"+entry["package"]["path"],
            "index_path":"module-catalog/index.json","contributing_url":CONTRIBUTING,
            "pull_request_url":REPOSITORY+"/compare"}
