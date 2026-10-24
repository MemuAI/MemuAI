"""MemuAI → Settings → «MemuNote» (Round 29): install / open / update / remove the optional AI notepad.

Layout of an installation made by the MemuAI Setup (``installer/products.py``)::

    <app>\\MemuAI\\MemuAI.exe             this process (frozen)
    <app>\\MemuNote\\MemuNote.exe         MemuNote when it is installed
    <app>\\Packages\\MemuNote.zip         MemuNote shipped "archived" when it was not selected in the Setup
    <app>\\install.json                   the Setup's state (``components`` / ``entries``)

Installing unpacks the zip (paths validated, staged next to the target and swapped
in with ``os.replace``), records the component in ``install.json`` so the next
Setup run keeps it, and adds a Start-menu shortcut. Removing packs the folder back
into ``Packages\\MemuNote.zip`` first, so it can be reinstalled without the Setup.
In a source checkout (not frozen) MemuNote runs as ``python memunote.py``.
``MEMUAI_INSTALL_DIR`` overrides the install folder (tests, portable layouts).

Routes: ``GET /api/pg/memunote`` (status), ``POST /api/pg/memunote/install``,
``/uninstall``, ``/open`` (``{files?: [..]}``), ``/open-with`` (``{enable}``).
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
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

log = logging.getLogger("memuai.memunote")

ROOT = Path(__file__).resolve().parents[1]
FOLDER = "MemuNote"
EXE = "MemuNote.exe"
PACKAGE = Path("Packages") / "MemuNote.zip"
PACKAGE_META = "memunote-package.json"
STATE_FILE = "install.json"
MAX_UNPACKED = 2 * 1024 ** 3          # a zip bomb guard: MemuNote is ~60 MB unpacked
_LOCK = threading.Lock()
_CREATE_NO_WINDOW = 0x08000000
_DETACHED = 0x00000008


class ManagerError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


# ------------------------------------------------------------------ locations

def install_root() -> Optional[Path]:
    """The MemuAI install folder, or ``None`` in a source checkout."""
    env = os.environ.get("MEMUAI_INSTALL_DIR", "").strip()
    if env:
        return Path(env)
    if not getattr(sys, "frozen", False):
        return None
    here = Path(sys.executable).resolve().parent
    for folder in (here.parent, here, here.parent.parent):
        if (folder / STATE_FILE).is_file() or (folder / "MemuAI-Uninstall.exe").is_file() or (folder / PACKAGE).is_file():
            return folder
    return here.parent


def paths(root: Optional[Path] = None) -> Dict[str, Optional[Path]]:
    root = root if root is not None else install_root()
    if root is None:
        return {"root": None, "folder": None, "exe": None, "package": None}
    return {"root": root, "folder": root / FOLDER, "exe": root / FOLDER / EXE, "package": root / PACKAGE}


def _dev_entry() -> Optional[Path]:
    entry = ROOT / "memunote.py"
    return entry if entry.is_file() and (ROOT / "app" / "memunote" / "static" / "index.html").is_file() else None


def _file_version(path: Path) -> str:
    """ProductVersion of a Windows exe (empty elsewhere / on any error)."""
    if os.name != "nt":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return ""
        buffer = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, buffer):
            return ""
        value = ctypes.c_void_p()
        length = wintypes.UINT()
        if not ctypes.windll.version.VerQueryValueW(buffer, "\\", ctypes.byref(value), ctypes.byref(length)):
            return ""

        class Fixed(ctypes.Structure):
            _fields_ = [(n, wintypes.DWORD) for n in ("sig", "struc", "ms", "ls", "pms", "pls", "mask", "flags",
                                                       "os", "type", "sub", "dms", "dls")]
        info = ctypes.cast(value, ctypes.POINTER(Fixed)).contents
        parts = [info.ms >> 16, info.ms & 0xFFFF, info.ls >> 16, info.ls & 0xFFFF]
        while len(parts) > 2 and parts[-1] == 0:
            parts.pop()
        return ".".join(str(p) for p in parts)
    except Exception:  # noqa: BLE001
        return ""


def package_info(package: Optional[Path]) -> Dict[str, Any]:
    if package is None or not package.is_file():
        return {"present": False}
    info: Dict[str, Any] = {"present": True, "size": package.stat().st_size, "version": ""}
    try:
        with zipfile.ZipFile(package) as archive:
            try:
                meta = json.loads(archive.read(PACKAGE_META).decode("utf-8"))
                info["version"] = str(meta.get("version") or "")[:40]
            except KeyError:
                pass
            info["unpacked"] = sum(item.file_size for item in archive.infolist())
            info["valid"] = EXE in archive.namelist()
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        info.update(valid=False, error=str(exc)[:200])
    return info


def _installed_version(folder: Path, exe: Path) -> str:
    marker = folder / PACKAGE_META
    if marker.is_file():
        try:
            return str(json.loads(marker.read_text(encoding="utf-8")).get("version") or "")[:40]
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
    """MemuNote publishes ``instance.json`` (port + token) while its window is open."""
    try:
        from .memunote.paths import note_dir
        data = json.loads((note_dir() / "instance.json").read_text(encoding="utf-8"))
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
                              "open_with": {"supported": False, "enabled": False}, "running": _running_instance()}
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
        try:
            from .memunote import winsys
            winsys.migrate()          # Round 31: drop the Round 29–30 script associations once (marker-guarded)
            result["open_with"] = winsys.open_with_state(str(exe))
        except Exception:  # noqa: BLE001
            pass
    result["package"] = package_info(p["package"])
    pkg_version = result["package"].get("version") or ""
    result["update_available"] = bool(result["installed"] and pkg_version and result["version"]
                                      and _newer(pkg_version, result["version"]))
    return result


def _newer(left: str, right: str) -> bool:
    def key(text: str):
        return [int(part) if part.isdigit() else 0 for part in text.replace("-", ".").split(".")[:4]]
    return key(left) > key(right)


# ------------------------------------------------------------------ install / remove

def _safe_member(name: str) -> Optional[str]:
    text = name.replace("\\", "/")
    if not text or text.startswith("/") or ":" in text.split("/", 1)[0]:
        return None
    parts = PurePosixPath(text).parts
    if any(part in ("..", "") for part in parts):
        return None
    return "/".join(parts)


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


def _run(exe: Path, args: List[str], timeout: float = 20.0) -> int:
    try:
        return subprocess.run([str(exe), *args], timeout=timeout, cwd=str(exe.parent),   # noqa: S603
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0).returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("%s %s failed: %s", exe.name, args, exc)
        return -1


def _remove_tree(path: Path) -> None:
    def onerror(func, target, _exc):
        try:
            os.chmod(target, 0o700)
            func(target)
        except OSError:
            pass
    for attempt in range(8):
        try:
            if path.is_dir():
                shutil.rmtree(path, onerror=onerror)
            elif path.exists():
                path.unlink()
            if not path.exists():
                return
        except OSError:
            pass
        time.sleep(0.25 * (attempt + 1))
    if path.exists():
        raise ManagerError("busy", f"could not remove {path} (is MemuNote still running?)")


def _update_state(root: Path, installed: bool) -> None:
    """Keep the Setup's install.json in step so an update neither drops nor duplicates MemuNote."""
    path = root / STATE_FILE
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(state, dict):
        return
    components = [c for c in list(state.get("components") or []) if c != "memunote"]
    entries = dict(state.get("entries") or {})
    entries.pop("memunote", None)
    if installed:
        components.append("memunote")
        entries["memunote"] = [FOLDER]
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
    link = programs / "MemuNote.lnk"
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
            "$s.Description = 'MemuNote'",
            "$s.Save()",
        ])
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],   # noqa: S603,S607
                       timeout=25, capture_output=True, creationflags=_CREATE_NO_WINDOW)
    except Exception as exc:  # noqa: BLE001 - a missing shortcut is not worth failing the install
        log.warning("MemuNote shortcut not created: %s", exc)


def install() -> Dict[str, Any]:
    with _LOCK:
        p = paths()
        if p["root"] is None:
            raise ManagerError("dev", "source checkout: run `python memunote.py`")
        root, folder, exe, package = p["root"], p["folder"], p["exe"], p["package"]
        assert folder is not None and exe is not None and package is not None
        if not package.is_file():
            raise ManagerError("no_package", f"{PACKAGE} was not found — run the MemuAI Setup and select MemuNote")
        updating = exe.is_file()
        if updating:
            _run(exe, ["--shutdown"], timeout=15)       # unsaved text is kept by MemuNote's recovery
        stage = root / f".~memunote-{os.getpid()}-{int(time.time())}"
        backup = root / f".~memunote-old-{os.getpid()}-{int(time.time())}"
        try:
            size = _extract(package, stage)
            if folder.exists():
                try:
                    os.replace(folder, backup)
                except OSError as exc:      # Windows: MemuNote.exe (or a file in it) is still in use
                    raise ManagerError("busy", f"could not replace {folder}: {exc.strerror or exc} "
                                               "(is MemuNote still running?)") from exc
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
            package.unlink()                             # the installed copy replaces the archive
            if not any(package.parent.iterdir()):
                package.parent.rmdir()
        except OSError:
            pass
        _update_state(root, True)
        _start_menu_shortcut(exe, True)
        log.info("MemuNote %s into %s (%d bytes)", "updated" if updating else "installed", folder, size)
        return {"ok": True, "updated": updating, **status()}


def _pack(folder: Path, package: Path, version: str) -> None:
    package.parent.mkdir(parents=True, exist_ok=True)
    partial = package.with_name(package.name + ".partial")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        archive.writestr(PACKAGE_META, json.dumps({"component": "memunote", "name": FOLDER, "version": version,
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
        if not folder.exists():
            return {"ok": True, **status()}
        if exe.is_file():
            _run(exe, ["--shutdown"], timeout=15)
            _run(exe, ["--unregister-open-with"], timeout=15)
        if keep_package and not package.is_file() and exe.is_file():
            try:
                _pack(folder, package, _installed_version(folder, exe) or _app_version())
            except OSError as exc:
                log.warning("MemuNote package not kept: %s", exc)
        _remove_tree(folder)
        _update_state(root, False)
        _start_menu_shortcut(None, False)
        return {"ok": True, **status()}


def open_app(files: Optional[List[str]] = None) -> Dict[str, Any]:
    files = [str(f) for f in (files or []) if isinstance(f, str) and f.strip()][:50]
    p = paths()
    if p["root"] is None:
        entry = _dev_entry()
        if entry is None:
            raise ManagerError("not_installed", "MemuNote is not installed")
        command = [sys.executable, str(entry), *files]
        cwd = str(ROOT)
    else:
        exe = p["exe"]
        if exe is None or not exe.is_file():
            raise ManagerError("not_installed", "MemuNote is not installed")
        command = [str(exe), *files]
        cwd = str(Path.home())
    flags = (_DETACHED | 0x00000200) if os.name == "nt" else 0          # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,   # noqa: S603
                     stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags,
                     start_new_session=os.name != "nt")
    return {"ok": True}


def set_open_with(enable: bool) -> Dict[str, Any]:
    p = paths()
    exe = p["exe"]
    if exe is None or not exe.is_file():
        raise ManagerError("not_installed", "MemuNote is not installed")
    from .memunote import winsys
    return winsys.register_open_with(bool(enable), exe=str(exe))


# ------------------------------------------------------------------ routes

def register(app) -> None:
    from fastapi import Request
    from fastapi.responses import JSONResponse

    def fail(exc: Exception):
        if isinstance(exc, ManagerError):
            status_code = 409 if exc.code in ("busy",) else 404 if exc.code in ("no_package", "not_installed") else 400
            return JSONResponse({"ok": False, "error": exc.code, "message": str(exc)}, status_code)
        log.exception("MemuNote manager failed")
        return JSONResponse({"ok": False, "error": "failed", "message": str(exc)[:300]}, 500)

    async def body_of(request: Request) -> Dict[str, Any]:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    @app.get("/api/pg/memunote")
    def memunote_status():
        return status()

    @app.post("/api/pg/memunote/install")
    def memunote_install():
        try:
            return install()
        except Exception as exc:  # noqa: BLE001
            return fail(exc)

    @app.post("/api/pg/memunote/uninstall")
    async def memunote_uninstall(request: Request):
        body = await body_of(request)
        from starlette.concurrency import run_in_threadpool
        try:
            return await run_in_threadpool(uninstall, body.get("keep_package", True) is not False)
        except Exception as exc:  # noqa: BLE001
            return fail(exc)

    @app.post("/api/pg/memunote/open")
    async def memunote_open(request: Request):
        body = await body_of(request)
        files = body.get("files") if isinstance(body.get("files"), list) else []
        try:
            return open_app(files)
        except Exception as exc:  # noqa: BLE001
            return fail(exc)

    @app.post("/api/pg/memunote/open-with")
    async def memunote_open_with(request: Request):
        body = await body_of(request)
        try:
            return set_open_with(bool(body.get("enable")))
        except Exception as exc:  # noqa: BLE001
            return fail(exc)
