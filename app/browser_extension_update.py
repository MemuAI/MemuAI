"""Keeps the unpacked Chrome extension current without a web store.

Chrome runs an unpacked extension straight from a folder and re-reads that
folder on ``chrome.runtime.reload()`` (and on every browser start). MemuAI
keeps one managed copy of the shipped bundle - either a folder it installs
itself or the folder the user extracted, proven by Chrome's path-derived
extension id - rewrites it when the shipped bundle changes and asks the
connected extension to reload itself.

Only MemuAI's own bundle is ever written, only into the managed folder or a
folder whose path hashes to the paired extension id, and never while the
extension is running a tool.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path

BUNDLE_FILE = "bundle.json"
RETRY_WINDOW = 3600.0
MAX_ATTEMPTS = 2
_lock = threading.Lock()
_cache: dict = {}


def source_root() -> Path:
    return Path(__file__).parent / "pgstatic" / "browser-extension"


def _files(root: Path) -> list[Path]:
    rows = [path for path in root.rglob("*")
            if path.is_file() and not path.is_symlink() and path.name != BUNDLE_FILE
            and "__pycache__" not in path.parts and not path.name.endswith((".lock", ".pyc"))]
    return sorted(rows, key=lambda path: path.relative_to(root).as_posix())


def bundle_info() -> dict:
    """Version from manifest.json plus a short content hash of every file."""
    root = source_root()
    files = _files(root)
    key = tuple((path.relative_to(root).as_posix(), stat.st_size, stat.st_mtime_ns)
                for path in files for stat in [path.stat()])
    with _lock:
        if _cache.get("key") == key:
            return dict(_cache["value"])
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    value = {"version": str((manifest or {}).get("version") or ""), "hash": digest.hexdigest()[:20]}
    with _lock:
        _cache.update(key=key, value=value)
    return dict(value)


def bundle_json(install_id: str = "") -> bytes:
    root = source_root()
    payload = {**bundle_info(), "install_id": install_id,
               "files": [path.relative_to(root).as_posix() for path in _files(root)]}
    return (json.dumps(payload, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def unpacked_id(path) -> str:
    """Chrome's id for an unpacked extension without a manifest key.

    SHA-256 over the absolute folder path (UTF-16LE with an upper-case drive
    letter on Windows, the resolved UTF-8 path elsewhere); the first 32 hex
    digits are mapped 0-9a-f -> a-p."""
    text = str(path)
    if os.name == "nt":
        text = os.path.abspath(text)
        if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
            text = text[0].upper() + text[1:]
        raw = text.encode("utf-16-le")
    else:
        raw = os.path.realpath(text).encode("utf-8")
    return "".join(chr(ord("a") + int(char, 16)) for char in hashlib.sha256(raw).hexdigest()[:32])


def default_dir() -> Path:
    from .pgdata import MEMU_DIR
    return Path(MEMU_DIR) / "chrome-extension"


def looks_like_bundle(folder: Path) -> bool:
    try:
        manifest = json.loads((Path(folder) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (isinstance(manifest, dict) and manifest.get("name") == "MemuAI"
            and (manifest.get("background") or {}).get("service_worker") == "worker.js")


def candidates() -> list[Path]:
    """Folders Chrome may run the extension from: the managed copy and the
    usual places a downloaded ZIP gets extracted to (one level deep)."""
    home = Path.home()
    rows: list[Path] = [default_dir()]
    for base in (home / "Downloads" / "MemuAI", home / "Downloads", home / "Desktop", home / "Documents", home):
        rows.append(base)
        try:
            children = sorted(base.iterdir())[:400] if base.is_dir() else []
        except OSError:
            children = []
        for child in children:
            name = child.name.lower()
            if name.startswith("memuai") and child.is_dir():
                rows.append(child)
                try:
                    rows.extend(sorted(item for item in child.iterdir() if item.is_dir() and item.name.lower().startswith("memuai"))[:20])
                except OSError:
                    pass
    seen, out = set(), []
    for row in rows:
        key = os.path.normcase(str(row))
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def find_loaded_folder(extension_id: str) -> Path | None:
    """The folder whose path Chrome hashed into this paired extension id."""
    if not extension_id:
        return None
    for folder in candidates():
        if folder.is_dir() and looks_like_bundle(folder) and unpacked_id(folder) == extension_id:
            return folder
    return None


def _atomic_write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if target.is_file() and target.read_bytes() == data:
            return
    except OSError:
        pass
    handle, temp = tempfile.mkstemp(prefix=".memu-", dir=str(target.parent))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temp, target)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def write_bundle(folder: Path, install_id: str) -> dict:
    """Copy the shipped bundle into ``folder``; stale files of the previous
    bundle (listed in its bundle.json) are removed, anything else is kept."""
    folder = Path(folder)
    root = source_root().resolve()
    if folder.resolve() == root or root in folder.resolve().parents:
        raise ValueError("Нельзя обновлять расширение внутри папки приложения")
    previous: list[str] = []
    try:
        old = json.loads((folder / BUNDLE_FILE).read_text(encoding="utf-8"))
        previous = [str(name) for name in (old.get("files") or []) if isinstance(name, str)]
    except (OSError, ValueError, AttributeError):
        pass
    files = _files(root)
    names = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        names.append(rel)
        _atomic_write(folder / rel, path.read_bytes())
    keep = set(names)
    for rel in previous:
        if rel in keep or rel.startswith(("/", "\\")) or ".." in Path(rel).parts:
            continue
        target = (folder / rel)
        try:
            if target.resolve().is_relative_to(folder.resolve()) and target.is_file():
                target.unlink()
        except OSError:
            pass
    _atomic_write(folder / BUNDLE_FILE, bundle_json(install_id))
    return {"path": str(folder), "files": len(names), **bundle_info()}


def client_info(auth: dict) -> dict:
    """What a connecting worker reported about itself (older builds report nothing)."""
    def text(key, limit):
        value = auth.get(key) if isinstance(auth, dict) else ""
        return str(value or "")[:limit] if isinstance(value, (str, int, float)) else ""
    return {"version": text("version", 32), "bundle": text("bundle", 64), "install_id": text("install_id", 64)}


def _config():
    from .browser_extension import settings
    return settings()


def _save(patch: dict) -> None:
    from .browser_extension import _update_config
    _update_config(patch)


def managed_folder(cfg: dict | None = None) -> Path | None:
    cfg = cfg if cfg is not None else _config()
    path = str(cfg.get("managed_path") or "")
    return Path(path) if path and Path(path).is_dir() else None


def install(folder: Path | None = None) -> dict:
    """Install (or refresh) the managed copy; keeps the install id stable."""
    cfg = _config()
    target = Path(folder) if folder else default_dir()
    install_id = str(cfg.get("managed_install_id") or "")
    if not install_id or os.path.normcase(str(cfg.get("managed_path") or "")) != os.path.normcase(str(target)):
        install_id = secrets.token_hex(12)
    result = write_bundle(target, install_id)
    _save({"managed_path": str(target), "managed_install_id": install_id})
    return {**result, "extension_id": unpacked_id(target)}


def _is_managed(folder: Path | None, cfg: dict, client: dict, extension_id: str) -> bool:
    """The connected copy runs from the managed folder: it carries that
    folder's install id, or Chrome derived its id from that folder's path."""
    if folder is None:
        return False
    install_id = str(cfg.get("managed_install_id") or "")
    if install_id and client.get("install_id") == install_id:
        return True
    try:
        return bool(extension_id) and unpacked_id(folder) == extension_id
    except (OSError, ValueError):
        return False


def runs_from_source(extension_id: str) -> bool:
    """A developer loaded the app's own folder: always the shipped code."""
    try:
        return bool(extension_id) and unpacked_id(source_root()) == extension_id
    except (OSError, ValueError):
        return False


def status(client: dict | None, connected: bool, cfg: dict | None = None, extension_id: str = "") -> dict:
    cfg = cfg if cfg is not None else _config()
    info = bundle_info()
    client = client or {}
    folder = managed_folder(cfg)
    managed = _is_managed(folder, cfg, client, extension_id)
    if not connected or not client:
        state = "unknown"
    elif client.get("bundle") == info["hash"] or runs_from_source(extension_id):
        state = "current"
    else:
        last = cfg.get("last_update") if isinstance(cfg.get("last_update"), dict) else {}
        if last.get("hash") == info["hash"] and last.get("state") == "restart":
            state = "restart"
        elif last.get("hash") == info["hash"] and last.get("state") == "stuck":
            state = "stuck"
        else:
            state = "managed" if managed else "manual"
    return {"state": state, "available": info["version"], "installed": str(client.get("version") or ""),
            "auto": cfg.get("auto_update", True) is not False, "managed": managed,
            "managed_path": str(folder) if folder else "", "legacy": bool(connected and client is not None and not client.get("bundle"))}


def plan(client: dict, extension_id: str, *, force: bool = False) -> dict:
    """Decide what to do for a connected, possibly outdated worker.

    Returns {"action": "none"|"reload"|"restart", "state": ...}. Files are
    rewritten here; the caller sends the reload command."""
    cfg = _config()
    info = bundle_info()
    if client.get("bundle") == info["hash"] or runs_from_source(extension_id):
        if isinstance(cfg.get("last_update"), dict) and cfg["last_update"].get("state") not in ("done", None):
            _save({"last_update": {**cfg["last_update"], "state": "done"}})
        return {"action": "none", "state": "current"}
    if not force and cfg.get("auto_update", True) is False:
        return {"action": "none", "state": "manual"}
    folder = managed_folder(cfg)
    install_id = str(cfg.get("managed_install_id") or "")
    managed = _is_managed(folder, cfg, client, extension_id)
    if not managed:
        adopted = find_loaded_folder(extension_id)
        if adopted is None:
            return {"action": "none", "state": "manual"}
        folder = adopted
        if os.path.normcase(str(cfg.get("managed_path") or "")) != os.path.normcase(str(adopted)) or not install_id:
            install_id = secrets.token_hex(12)
        _save({"managed_path": str(adopted), "managed_install_id": install_id})
    last = cfg.get("last_update") if isinstance(cfg.get("last_update"), dict) else {}
    now = time.time()
    legacy = not client.get("bundle")
    if legacy and last.get("hash") == info["hash"] and last.get("state") == "restart" and not force:
        return {"action": "none", "state": "restart", "path": str(folder)}
    attempts = int(last.get("attempts") or 0) if last.get("hash") == info["hash"] and now - float(last.get("at") or 0) < RETRY_WINDOW else 0
    if attempts >= MAX_ATTEMPTS and not force:
        _save({"last_update": {**last, "state": "stuck"}})
        return {"action": "none", "state": "stuck"}
    write_bundle(folder, install_id)
    state = "restart" if legacy else "reloading"
    _save({"last_update": {"hash": info["hash"], "version": info["version"], "from": client.get("version") or "",
                           "at": now, "attempts": attempts + 1, "state": state}})
    return {"action": "restart" if legacy else "reload", "state": state, "path": str(folder)}
