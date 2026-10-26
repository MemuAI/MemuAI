"""MemuAI → Settings → «MemuFiles» (Round 35): install / open / update / remove the optional file manager.

Same layout and lifecycle as MemuNote (:mod:`app.memunote_manager`)::

    <app>\\MemuFiles\\MemuFiles.exe        MemuFiles when it is installed
    <app>\\Packages\\MemuFiles.zip         shipped "archived" when it was not selected in the Setup
    <app>\\install.json                    the Setup's state (``components`` / ``entries``)

The Setup writes the package metadata as ``memufiles-package.json`` (``installer/engine.py``
names it ``<component>-package.json``; MemuNote keeps its historical ``memunote-package.json``).
Older packages without the file are still accepted (the version then comes from the exe).
In a source checkout MemuFiles runs as ``python memufiles.py``; ``MEMUAI_INSTALL_DIR`` overrides
the install folder (tests, portable layouts).

Routes: ``GET /api/pg/memufiles`` (status), ``POST /api/pg/memufiles/install``, ``/uninstall``,
``/open`` (``{paths?: [..]}`` — folders open as tabs, a file opens its folder with the file selected),
``GET|POST /api/pg/memufiles/default-shell`` (Round 38, «MemuFiles по умолчанию»: :mod:`app.memufiles.default_shell`;
removing MemuFiles restores Explorer first).
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .memunote_manager import (ManagerError, _file_version, _newer, _remove_tree, _run, _safe_member,
                               install_root as _note_install_root)

log = logging.getLogger("memuai.memufiles")

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = "memufiles"
FOLDER = "MemuFiles"
EXE = "MemuFiles.exe"
PACKAGE = Path("Packages") / "MemuFiles.zip"
PACKAGE_META = "memufiles-package.json"
STATE_FILE = "install.json"
MAX_UNPACKED = 2 * 1024 ** 3
_LOCK = threading.Lock()
_CREATE_NO_WINDOW = 0x08000000
_DETACHED = 0x00000008

def install_root() -> Optional[Path]:
    """The MemuAI install folder (``None`` in a source checkout) — the same folder MemuNote uses."""
    root = _note_install_root()
    if root is None or os.environ.get("MEMUAI_INSTALL_DIR", "").strip():
        return root
    here = Path(sys.executable).resolve().parent
    for folder in (here.parent, here, here.parent.parent):
        if (folder / PACKAGE).is_file() or (folder / FOLDER / EXE).is_file():
            return folder
    return root

def paths(root: Optional[Path] = None) -> Dict[str, Optional[Path]]:
    root = root if root is not None else install_root()
    if root is None:
        return {"root": None, "folder": None, "exe": None, "package": None}
    return {"root": root, "folder": root / FOLDER, "exe": root / FOLDER / EXE, "package": root / PACKAGE}

def _dev_entry() -> Optional[Path]:
    entry = ROOT / "memufiles.py"
    return entry if entry.is_file() and (ROOT / "app" / "memufiles" / "static" / "index.html").is_file() else None

def package_info(package: Optional[Path]) -> Dict[str, Any]:
    if package is None or not package.is_file():
        return {"present": False}
    info: Dict[str, Any] = {"present": True, "size": package.stat().st_size, "version": ""}
    try:
        with zipfile.ZipFile(package) as archive:
            names = archive.namelist()
            for meta_name in (PACKAGE_META, "memunote-package.json"):
                if meta_name not in names:
                    continue
                try:
                    meta = json.loads(archive.read(meta_name).decode("utf-8"))
                except ValueError:
                    continue
                if meta.get("component") in (None, COMPONENT):
                    info["version"] = str(meta.get("version") or "")[:40]
                    break
            info["unpacked"] = sum(item.file_size for item in archive.infolist())
            info["valid"] = EXE in names
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        info.update(valid=False, error=str(exc)[:200])
    return info

def _installed_version(folder: Path, exe: Path) -> str:
    for name in (PACKAGE_META, "memunote-package.json"):
        marker = folder / name
        if marker.is_file():
            try:
                meta = json.loads(marker.read_text(encoding="utf-8"))
                if meta.get("component") in (None, COMPONENT):
                    return str(meta.get("version") or "")[:40]
            except (OSError, ValueError):
                pass
    return _file_version(exe)

def _app_version() -> str:
    try:
        from .version import APP_VERSION  # type: ignore
        return str(APP_VERSION)
    except Exception:  # noqa: BLE001
        return ""

def _running_instance() -> bool:
    """MemuFiles publishes ``instance.json`` (port + token + pid) while it runs."""
    try:
        from .memufiles.paths import files_dir
        data = json.loads((files_dir() / "instance.json").read_text(encoding="utf-8"))
        pid = int(data.get("pid") or 0)
        if not data.get("port") or pid <= 0:
            return False
        try:
            import psutil  # type: ignore
            return bool(psutil.pid_exists(pid))
        except ImportError:
            return True
    except Exception:  # noqa: BLE001
        return False

def status() -> Dict[str, Any]:
    p = paths()
    dev = p["root"] is None
    result: Dict[str, Any] = {"dev": dev, "platform": os.name, "app_version": _app_version(),
                              "installed": False, "version": "", "package": {"present": False},
                              "running": _running_instance()}
    if dev:
        entry = _dev_entry()
        result.update(installed=entry is not None, source=str(entry or ""))
        return result
    folder, exe = p["folder"], p["exe"]
    assert folder is not None and exe is not None
    result["root"] = str(p["root"])
    result["installed"] = exe.is_file()
    if result["installed"]:
        result["path"] = str(exe)
        result["version"] = _installed_version(folder, exe)
    result["package"] = package_info(p["package"])
    pkg_version = result["package"].get("version") or ""
    result["update_available"] = bool(result["installed"] and pkg_version and result["version"]
                                      and _newer(pkg_version, result["version"]))
    return result

def _extract(package: Path, target: Path) -> int:
    total = 0
    with zipfile.ZipFile(package) as archive:
        members = archive.infolist()
        if EXE not in archive.namelist():
            raise ManagerError("package_invalid", f"{EXE} is missing in {package.name}")
        if sum(item.file_size for item in members) > MAX_UNPACKED:
            raise ManagerError("package_invalid", "the package is too large")
        for item in members:
            name = _safe_member(item.filename)
            if name is None:
                raise ManagerError("package_invalid", f"unsafe path in the package: {item.filename!r}")
            destination = target / name
            if item.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, open(destination, "wb") as sink:
                shutil.copyfileobj(source, sink, 1024 * 1024)
            total += item.file_size
    return total

def _update_state(root: Path, installed: bool) -> None:
    """Keep the Setup's install.json in step so an update neither drops nor duplicates MemuFiles."""
    path = root / STATE_FILE
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(state, dict):
        return
    components = [c for c in list(state.get("components") or []) if c != COMPONENT]
    entries = dict(state.get("entries") or {})
    entries.pop(COMPONENT, None)
    if installed:
        components.append(COMPONENT)
        entries[COMPONENT] = [FOLDER]
    state["components"], state["entries"] = components, entries
    fd, tmp = tempfile.mkstemp(prefix=STATE_FILE + ".", suffix=".tmp", dir=str(root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("install.json not updated: %s", exc)
        try:
            os.unlink(tmp)
        except OSError:
            pass

def _start_menu_shortcut(exe: Optional[Path], create: bool) -> None:
    if os.name != "nt":
        return
    programs = Path(os.environ.get("APPDATA") or Path.home()) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "MemuAI"
    link = programs / "MemuFiles.lnk"
    if not create:
        try:
            link.unlink()
        except OSError:
            pass
        return
    if exe is None:
        return
    try:
        programs.mkdir(parents=True, exist_ok=True)
        q = lambda value: str(value).replace("'", "''")   # noqa: E731
        script = "; ".join([
            "$shell = New-Object -ComObject WScript.Shell",
            f"$s = $shell.CreateShortcut('{q(link)}')",
            f"$s.TargetPath = '{q(exe)}'",
            f"$s.WorkingDirectory = '{q(Path.home())}'",
            f"$s.IconLocation = '{q(exe)},0'",
            "$s.Description = 'MemuFiles'",
            "$s.Save()",
        ])
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],   # noqa: S603,S607
                       timeout=25, capture_output=True, creationflags=_CREATE_NO_WINDOW)
    except Exception as exc:  # noqa: BLE001 - a missing shortcut is not worth failing the install
        log.warning("MemuFiles shortcut not created: %s", exc)

def install() -> Dict[str, Any]:
    with _LOCK:
        p = paths()
        if p["root"] is None:
            raise ManagerError("dev", "source checkout: run `python memufiles.py`")
        root, folder, exe, package = p["root"], p["folder"], p["exe"], p["package"]
        assert folder is not None and exe is not None and package is not None
        if not package.is_file():
            raise ManagerError("no_package", f"{PACKAGE} was not found — run the MemuAI Setup and select MemuFiles")
        updating = exe.is_file()
        if updating:
            _run(exe, ["--shutdown"], timeout=15)
        stamp = f"{os.getpid()}-{int(time.time())}"
        stage = root / f".~memufiles-{stamp}"
        backup = root / f".~memufiles-old-{stamp}"
        try:
            size = _extract(package, stage)
            if folder.exists():
                try:
                    os.replace(folder, backup)
                except OSError as exc:
                    raise ManagerError("busy", f"could not replace {folder}: {exc.strerror or exc} "
                                               "(is MemuFiles still running?)") from exc
            os.replace(stage, folder)
        except Exception:
            if backup.exists() and not folder.exists():
                os.replace(backup, folder)
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
            raise
        if backup.exists():
            try:
                _remove_tree(backup)
            except ManagerError as exc:
                log.warning("%s", exc)
        try:
            package.unlink()
            if not any(package.parent.iterdir()):
                package.parent.rmdir()
        except OSError:
            pass
        _update_state(root, True)
        _start_menu_shortcut(exe, True)
        log.info("MemuFiles %s into %s (%d bytes)", "updated" if updating else "installed", folder, size)
        return {"ok": True, "updated": updating, **status()}

def _pack(folder: Path, package: Path, version: str) -> None:
    package.parent.mkdir(parents=True, exist_ok=True)
    partial = package.with_name(package.name + ".partial")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        archive.writestr(PACKAGE_META, json.dumps({"component": COMPONENT, "name": FOLDER, "version": version,
                                                   "exe": EXE, "format": 1}))
        for path in sorted(folder.rglob("*")):
            relative = path.relative_to(folder).as_posix()
            if relative == PACKAGE_META:
                continue
            if path.is_dir():
                archive.writestr(relative + "/", b"")
            else:
                archive.write(path, relative)
    os.replace(partial, package)

def uninstall(keep_package: bool = True) -> Dict[str, Any]:
    with _LOCK:
        p = paths()
        if p["root"] is None:
            raise ManagerError("dev", "source checkout")
        root, folder, exe, package = p["root"], p["folder"], p["exe"], p["package"]
        assert folder is not None and exe is not None and package is not None
        _restore_explorer()
        if not folder.exists():
            return {"ok": True, **status()}
        if exe.is_file():
            _run(exe, ["--shutdown"], timeout=15)
        if keep_package and not package.is_file() and exe.is_file():
            try:
                _pack(folder, package, _installed_version(folder, exe) or _app_version())
            except OSError as exc:
                log.warning("MemuFiles package not kept: %s", exc)
        _remove_tree(folder)
        _update_state(root, False)
        _start_menu_shortcut(None, False)
        return {"ok": True, **status()}

def _restore_explorer() -> None:
    try:
        from .memufiles import default_shell
        result = default_shell.restore_all()
        if result.get("problems"):
            log.warning("Explorer restore: %s", result["problems"])
    except Exception as exc:  # noqa: BLE001 - the launcher still falls back to Explorer
        log.warning("Explorer restore failed: %s", exc)

def _clean_paths(values: Any) -> List[str]:
    out: List[str] = []
    for value in (values if isinstance(values, list) else [])[:20]:
        if not isinstance(value, str):
            continue
        text = value.strip().strip('"')
        if not text or "\x00" in text or len(text) > 4096 or text.startswith("-") or "://" in text:
            continue
        if not os.path.isabs(text):
            continue
        out.append(os.path.normpath(text))
    return out

def open_app(paths_to_open: Optional[List[str]] = None) -> Dict[str, Any]:
    """Start MemuFiles (or hand the paths to the running window: MemuFiles forwards them itself)."""
    targets = _clean_paths(paths_to_open)
    p = paths()
    if p["root"] is None:
        entry = _dev_entry()
        if entry is None:
            raise ManagerError("not_installed", "MemuFiles is not installed")
        command = [sys.executable, str(entry), *targets]
        cwd = str(ROOT)
    else:
        exe = p["exe"]
        if exe is None or not exe.is_file():
            raise ManagerError("not_installed", "MemuFiles is not installed")
        command = [str(exe), *targets]
        cwd = str(Path.home())
    flags = (_DETACHED | 0x00000200) if os.name == "nt" else 0
    subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,   # noqa: S603
                     stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags,
                     start_new_session=os.name != "nt")
    return {"ok": True, "paths": targets}

def available() -> bool:
    """MemuFiles can be opened right now (installed, or running from a source checkout)."""
    try:
        p = paths()
        if p["root"] is None:
            return _dev_entry() is not None
        return bool(p["exe"] and p["exe"].is_file())
    except Exception:  # noqa: BLE001
        return False

def register(app) -> None:
    from fastapi import Request
    from fastapi.responses import JSONResponse

    def fail(exc: Exception):
        if isinstance(exc, ManagerError):
            status_code = 409 if exc.code in ("busy",) else 404 if exc.code in ("no_package", "not_installed") else 400
            return JSONResponse({"ok": False, "error": exc.code, "message": str(exc)}, status_code)
        log.exception("MemuFiles manager failed")
        return JSONResponse({"ok": False, "error": "failed", "message": str(exc)[:300]}, 500)

    async def body_of(request: Request) -> Dict[str, Any]:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    @app.get("/api/pg/memufiles")
    def memufiles_status():
        return status()

    @app.post("/api/pg/memufiles/install")
    async def memufiles_install():
        from starlette.concurrency import run_in_threadpool
        try:
            return await run_in_threadpool(install)
        except Exception as exc:  # noqa: BLE001
            return fail(exc)

    @app.post("/api/pg/memufiles/uninstall")
    async def memufiles_uninstall(request: Request):
        body = await body_of(request)
        from starlette.concurrency import run_in_threadpool
        try:
            return await run_in_threadpool(uninstall, body.get("keep_package", True) is not False)
        except Exception as exc:  # noqa: BLE001
            return fail(exc)

    @app.get("/api/pg/memufiles/default-shell")
    def memufiles_default_shell_status():
        from .memufiles import default_shell
        return default_shell.status()

    @app.post("/api/pg/memufiles/default-shell")
    async def memufiles_default_shell(request: Request):
        """Round 38: ``{action: enable|disable|restore|hook, feature?: folders|win_e, confirmed?: true, on?}``."""
        from starlette.concurrency import run_in_threadpool
        from .memufiles import default_shell
        return await run_in_threadpool(default_shell.handle, await body_of(request))

    @app.post("/api/pg/memufiles/open")
    async def memufiles_open(request: Request):
        body = await body_of(request)
        items = body.get("paths") if isinstance(body.get("paths"), list) else ([body["path"]] if isinstance(body.get("path"), str) else [])
        try:
            return open_app(items)
        except Exception as exc:  # noqa: BLE001
            return fail(exc)
