"""MemuAI installer engine: pure, testable install / upgrade / uninstall logic.

Everything that touches Windows itself (registry, shortcuts, processes,
environment broadcast, known folders) goes through a :class:`Platform` object;
``winplat.WindowsPlatform`` is the real one, the tests use a fake. File-system
work (extraction, swapping folders, deleting) is done here directly, with
extended-length paths on Windows so deep PyInstaller trees never hit MAX_PATH.

Install transaction (per component, nothing is overlaid in place):

1. close running programs (graceful ``--shutdown`` of each exe, which uses the
   apps' own token-protected ``/api/window/shutdown``; force only with consent
   or in silent mode, and only processes whose image lives in the install dir);
2. extract the selected components into ``<app>\\.~stage-XXXX`` (SHA-256 verified);
3. swap: every top-level entry the component owns is moved to ``.~old-XXXX``
   and the staged one moved in; any failure rolls the swap back;
4. register: uninstaller, ``install.json``, shortcuts, Run values, PATH,
   the “Installed apps” entry; drop deselected components and old layouts.

Personal data (``%APPDATA%\\MemuAI`` …) is never touched by install or
upgrade; uninstall removes it only when the user explicitly asks.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import secrets
import shlex
import shutil
import stat
import sys
import threading
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import i18n
from .payload import Payload, PayloadError, strip_signature_directory
from .products import RUN_KEY, UNINSTALL_ROOT, Component, Product
from . import versions

log = logging.getLogger("memuai.setup")

STATE_FILE = "install.json"
LOG_FILE = "install.log"
STAGE_PREFIX = ".~stage-"
BACKUP_PREFIX = ".~old-"
SPACE_MARGIN = 64 * 1024 * 1024
HKCU = "HKCU"
HKLM = "HKLM"
IS_WINDOWS = os.name == "nt"

class SetupError(Exception):
    """An error with a user-facing i18n code (shown by the UI, logged in silent mode)."""

    def __init__(self, code: str, message: str = "", **details: object) -> None:
        super().__init__(message or code)
        self.code = code
        self.details = details

class AppsRunning(SetupError):
    def __init__(self, apps: Sequence[str]) -> None:
        super().__init__("apps_running", "programs are still running: " + ", ".join(apps), apps=list(apps))
        self.apps = list(apps)

class Cancelled(SetupError):
    def __init__(self) -> None:
        super().__init__("cancelled", "cancelled by the user")

def parse_version(text: str) -> Tuple[Tuple[int, ...], int, str]:
    """``"2.1"`` -> ``((2, 1, 0, 0), 1, "")``; a pre-release suffix sorts before the release."""
    raw = str(text or "").strip().lstrip("vV")
    match = re.match(r"^(\d+(?:\.\d+)*)(.*)$", raw)
    if not match:
        return (0, 0, 0, 0), (0 if raw else 1), raw.lower()
    numbers = [int(part) for part in match.group(1).split(".")][:4]
    numbers += [0] * (4 - len(numbers))
    suffix = match.group(2).strip(" -_.+")
    return tuple(numbers), (0 if suffix else 1), suffix.lower()

def compare_versions(left: str, right: str) -> int:
    a, b = parse_version(left), parse_version(right)
    return (a > b) - (a < b)

def long_path(path: os.PathLike | str) -> str:
    """Extended-length form on Windows (``\\\\?\\C:\\...``), unchanged elsewhere."""
    text = os.path.abspath(os.fspath(path))
    if not IS_WINDOWS or text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text

def same_path(left: str, right: str) -> bool:
    def norm(value: str) -> str:
        value = os.path.expandvars(str(value or "").strip().strip('"'))
        value = value.replace("/", "\\").rstrip("\\")
        return value.casefold()
    return bool(left) and norm(left) == norm(right)

def remove_path_entry(paths: str, entry: str) -> str:
    """The PATH string without *entry* (case-insensitive, trailing backslash ignored)."""
    return ";".join(item for item in str(paths or "").split(";") if item.strip() and not same_path(item, entry))

def add_path_entry(paths: str, entry: str) -> str:
    items = [item for item in str(paths or "").split(";") if item.strip()]
    if any(same_path(item, entry) for item in items):
        return ";".join(items)
    return ";".join(items + [entry])

def _rmtree(path: Path) -> None:
    def onerror(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            raise
    if sys.version_info >= (3, 12):
        shutil.rmtree(long_path(path), onexc=onerror)
    else:  # pragma: no cover - older runtimes
        shutil.rmtree(long_path(path), onerror=onerror)

def remove_entry(path: Path) -> None:
    lp = long_path(path)
    if os.path.isdir(lp) and not os.path.islink(lp):
        _rmtree(path)
    elif os.path.lexists(lp):
        try:
            os.remove(lp)
        except PermissionError:
            os.chmod(lp, stat.S_IWRITE)
            os.remove(lp)

def exists(path: Path) -> bool:
    return os.path.lexists(long_path(path))

def retry(action: Callable[[], object], attempts: int = 12, delay: float = 0.25,
          sleep: Callable[[float], None] = time.sleep):
    """Retry a file operation: antivirus scanners briefly lock freshly written files."""
    for attempt in range(attempts):
        try:
            return action()
        except OSError:
            if attempt == attempts - 1:
                raise
            sleep(delay * (1 + attempt * 0.5))
    return None

class Platform:
    """Interface to the operating system. Every method may raise OSError."""

    def env(self, name: str) -> str:
        return os.environ.get(name, "")

    def known_folder(self, name: str) -> Path:
        raise NotImplementedError

    def reg_get(self, root: str, key: str, name: str):
        raise NotImplementedError

    def reg_values(self, root: str, key: str) -> Dict[str, object]:
        raise NotImplementedError

    def reg_set(self, root: str, key: str, name: str, value, kind: str = "sz") -> None:
        raise NotImplementedError

    def reg_delete_value(self, root: str, key: str, name: str) -> None:
        raise NotImplementedError

    def reg_delete_key(self, root: str, key: str) -> None:
        raise NotImplementedError

    def create_shortcut(self, link: Path, target: Path, args: str = "", workdir: str = "",
                        icon: str = "", description: str = "", app_id: str = "") -> None:
        raise NotImplementedError

    def processes_in(self, folder: Path) -> List[Tuple[int, str]]:
        return []

    def directory_users(self, folder: Path) -> List[Tuple[int, str]]:
        """External programs whose working directory prevents replacing this tree."""
        return []

    def terminate(self, pid: int) -> None:
        raise NotImplementedError

    def run_wait(self, exe: Path, args: Sequence[str], timeout: float) -> int:
        raise NotImplementedError

    def launch(self, exe: Path, args: Sequence[str], cwd: Optional[Path] = None) -> None:
        raise NotImplementedError

    def broadcast_environment(self) -> None:
        pass

    def refresh_shell_icons(self, paths: Sequence[Path]) -> None:
        """Round 33: tell Explorer these files' icons changed (no-op off Windows)."""

    def disk_free(self, path: Path) -> int:
        probe = Path(path)
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        return shutil.disk_usage(str(probe)).free

    def webview2_version(self) -> str:
        return ""

    def file_version(self, path: Path) -> str:
        """File-version resource of an exe (``"a.b.c.d"``) or ``""``; pure Python here."""
        return versions.pe_file_version(path)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

class PortablePlatform(Platform):
    """Development stand-in on non-Windows systems (``--ui-dev`` runs, frozen smoke tests).

    The "registry" is a JSON file and shortcuts are small JSON files, all under
    ``$MEMUAI_SETUP_SANDBOX`` (default ``~/.memuai-setup-sandbox``). Never used on Windows.
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root or os.environ.get("MEMUAI_SETUP_SANDBOX") or Path.home() / ".memuai-setup-sandbox")
        self.root.mkdir(parents=True, exist_ok=True)
        self._file = self.root / "registry.json"

    def env(self, name: str) -> str:
        defaults = {"LOCALAPPDATA": "Local", "APPDATA": "Roaming", "USERPROFILE": "home", "TEMP": "Temp"}
        if name in defaults:
            path = self.root / defaults[name]
            path.mkdir(parents=True, exist_ok=True)
            return str(path)
        return os.environ.get(name, "")

    def known_folder(self, name: str) -> Path:
        path = self.root / ("StartMenu" if name == "programs" else "Desktop")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _load(self) -> Dict[str, Dict[str, object]]:
        try:
            return json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: Dict[str, Dict[str, object]]) -> None:
        self._file.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def reg_get(self, root: str, key: str, name: str):
        return self._load().get(f"{root}\\{key}", {}).get(name)

    def reg_values(self, root: str, key: str) -> Dict[str, object]:
        return dict(self._load().get(f"{root}\\{key}", {}))

    def reg_set(self, root: str, key: str, name: str, value, kind: str = "sz") -> None:
        data = self._load()
        data.setdefault(f"{root}\\{key}", {})[name] = value
        self._save(data)

    def reg_delete_value(self, root: str, key: str, name: str) -> None:
        data = self._load()
        data.get(f"{root}\\{key}", {}).pop(name, None)
        self._save(data)

    def reg_delete_key(self, root: str, key: str) -> None:
        data = self._load()
        data.pop(f"{root}\\{key}", None)
        self._save(data)

    def create_shortcut(self, link: Path, target: Path, args: str = "", workdir: str = "",
                        icon: str = "", description: str = "", app_id: str = "") -> None:
        Path(link).write_text(json.dumps({"target": str(target), "args": args, "workdir": workdir}), encoding="utf-8")

    def run_wait(self, exe: Path, args: Sequence[str], timeout: float) -> int:
        return 0

    def launch(self, exe: Path, args: Sequence[str], cwd: Optional[Path] = None) -> None:
        log.info("would launch %s %s", exe, " ".join(args))

    def terminate(self, pid: int) -> None:
        pass

@dataclass
class Options:
    install_dir: str
    components: List[str]
    tasks: List[str]
    language: str = "en"
    launch: bool = False

@dataclass
class Existing:
    install_dir: str = ""
    version: str = ""
    components: List[str] = field(default_factory=list)
    tasks: List[str] = field(default_factory=list)
    language: str = ""
    source: str = ""
    entries: Dict[str, List[str]] = field(default_factory=dict)
    legacy_machine: bool = False

    @property
    def found(self) -> bool:
        return bool(self.source)

Progress = Callable[[Dict[str, object]], None]

DEFAULT_CLOSE_WAIT = 8.0

class Engine:
    def __init__(self, product: Product, platform: Platform, payload: Optional[Payload] = None,
                 version: str = "", stub_source: Optional[Path] = None,
                 close_wait: float = DEFAULT_CLOSE_WAIT) -> None:
        self.product = product
        self.os = platform
        self.payload = payload
        self.version = version or (str(payload.meta.get("version", "")) if payload else "")

        self.stub_source = stub_source
        self.cancel_event = threading.Event()
        self.warnings: List[str] = []

        self.close_wait = max(0.0, float(close_wait))

    @property
    def uninstall_key(self) -> str:
        return UNINSTALL_ROOT + "\\" + self.product.reg_key

    def default_dir(self) -> str:
        base = self.os.env("LOCALAPPDATA") or os.path.join(self.os.env("USERPROFILE") or str(Path.home()),
                                                            "AppData", "Local")
        return os.path.join(base, "Programs", self.product.default_dir_name)

    def _reg_values(self, root: str, key: str) -> Dict[str, object]:
        try:
            return self.os.reg_values(root, key) or {}
        except OSError:
            return {}

    def detect(self) -> Existing:
        found = Existing()
        values = self._reg_values(HKCU, self.uninstall_key)
        location = str(values.get("InstallLocation") or values.get("Inno Setup: App Path") or "").strip().strip('"')
        if location:
            found.install_dir = location.rstrip("\\/") or location
            found.version = str(values.get("DisplayVersion") or "")
            found.source = "inno" if "Inno Setup: App Path" in values else "memuai"
            comps = str(values.get("Inno Setup: Selected Components") or "")
            tasks = str(values.get("Inno Setup: Selected Tasks") or "")
            found.components = [c for c in comps.split(",") if c]
            found.tasks = [t for t in tasks.split(",") if t]
            found.language = str(values.get("Inno Setup: Language") or "")
        state = self.read_state(Path(found.install_dir)) if found.install_dir else None
        if state is None:
            candidate = Path(self.default_dir())
            state = self.read_state(candidate)
            if state is not None and not found.install_dir:
                found.install_dir = str(candidate)
        if state is not None:
            found.source = "memuai"
            found.version = str(state.get("version") or found.version)
            found.components = list(state.get("components") or found.components)
            found.tasks = list(state.get("tasks") or found.tasks)
            found.language = str(state.get("language") or found.language)
            found.entries = {k: list(v) for k, v in dict(state.get("entries") or {}).items()}
        if found.install_dir and not found.components:
            present = [c.id for c in self.product.components
                       if c.exe and exists(Path(found.install_dir) / c.exe.replace("\\", os.sep))]
            if present:
                found.components = present
                found.source = found.source or "files"
        if found.install_dir and found.components:

            for comp_id, _zip in self.product.packages:
                comp = self.product.component(comp_id)
                if comp_id not in found.components and comp.exe and \
                        exists(Path(found.install_dir) / comp.exe.replace("\\", os.sep)):
                    found.components.append(comp_id)
        if found.install_dir and not exists(Path(found.install_dir)):

            found.source, found.components, found.version = "", [], ""
        found.version = versions.normalize_version(found.version)
        if found.found and not found.version:
            found.version = self._installed_version_fallback(found, values)
        if self.product.legacy_machine_key:
            key = UNINSTALL_ROOT + "\\" + self.product.legacy_machine_key
            for root in ("HKLM64", "HKLM32"):
                try:
                    if self.os.reg_get(root, key, "InstallLocation"):
                        found.legacy_machine = True
                except OSError:
                    pass
        return found

    def _installed_version_fallback(self, found: Existing, values: Dict[str, object]) -> str:
        """No usable ``install.json``/``DisplayVersion``: ask the installed exe, then Inno's DWORDs."""
        root = Path(found.install_dir)
        candidates = [c.exe for c in self.product.components if c.exe]
        candidates += [name for name in self.product.legacy_files if name.lower().endswith(".exe")]
        for relative in candidates:
            exe = root / relative.replace("\\", os.sep)
            if not exists(exe):
                continue
            try:
                number = versions.normalize_version(self.os.file_version(exe))
            except Exception:  # noqa: BLE001 - a broken exe must not stop the Setup
                number = ""
            if number:
                log.info("installed version %s read from %s", number, exe)
                return number
        return versions.from_major_minor(values.get("MajorVersion"), values.get("MinorVersion"))

    def _run_value_present(self, name: str) -> bool:
        try:
            return bool(self.os.reg_get(HKCU, RUN_KEY, name))
        except OSError:
            return False

    def defaults(self, existing: Optional[Existing] = None, language: str = "en") -> Options:
        existing = existing or self.detect()
        product = self.product
        if existing.found and existing.components:
            components = list(existing.components)
        else:
            components = product.default_components()
        if existing.found and existing.source in ("memuai", "inno") and existing.tasks is not None:
            tasks = list(existing.tasks)
            if existing.source == "inno" and not existing.tasks:
                tasks = product.default_tasks()
        else:
            tasks = product.default_tasks()

        for run in product.run_values:
            if self._run_value_present(run.name) and run.task not in tasks:
                tasks.append(run.task)
        components, tasks = product.normalize(components, tasks)
        lang = existing.language if existing.language in ("ru", "en") else language
        return Options(install_dir=existing.install_dir or self.default_dir(), components=components,
                       tasks=tasks, language=lang)

    def install_kind(self, existing: Existing) -> str:
        """``fresh`` | ``upgrade`` | ``reinstall`` | ``downgrade`` (see :mod:`installer.versions`)."""
        return versions.classify(self.version, existing.version, existing.found)

    @staticmethod
    def read_state(app_dir: Path) -> Optional[dict]:
        try:
            with open(long_path(Path(app_dir) / STATE_FILE), encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def required_bytes(self, components: Iterable[str]) -> int:
        if not self.payload:
            return 0
        return self.payload.index.size_of(self.product.normalize(components, [])[0])

    def check_dir(self, path: str, components: Iterable[str]) -> Dict[str, object]:
        """Validate a folder chosen in the UI. Returns ``{ok, path, error?, free, required, adjusted}``."""
        text = os.path.expandvars(str(path or "").strip().strip('"'))
        result: Dict[str, object] = {"ok": False, "path": text, "adjusted": False,
                                     "required": self.required_bytes(components), "free": 0}
        if not text:
            result["error"] = "dir_empty"
            return result
        if IS_WINDOWS and not re.match(r"^(?:[A-Za-z]:\\|\\\\[^\\]+\\[^\\]+)", text):
            result["error"] = "dir_not_absolute"
            return result
        if not IS_WINDOWS and not os.path.isabs(text):
            result["error"] = "dir_not_absolute"
            return result
        if re.search(r'[<>"|?*]', text[2:] if IS_WINDOWS else text):
            result["error"] = "dir_invalid"
            return result
        target = Path(os.path.normpath(text))
        result["path"] = str(target)

        protected = {self.os.env(name).rstrip("\\/").casefold() for name in
                     ("USERPROFILE", "LOCALAPPDATA", "APPDATA", "ProgramFiles", "ProgramFiles(x86)",
                      "SystemRoot", "TEMP") if self.os.env(name)}
        protected.add(os.path.join(self.os.env("LOCALAPPDATA"), "Programs").rstrip("\\/").casefold())
        is_root = target.parent == target
        foreign = False
        try:
            if exists(target) and os.path.isdir(long_path(target)):
                names = os.listdir(long_path(target))
                owned = {STATE_FILE.casefold(), self.product.uninstaller.casefold(), "unins000.exe"}
                for comp in self.product.components:
                    if comp.dest:
                        owned.add(comp.dest.casefold())
                    else:
                        owned.add(comp.exe.casefold())
                foreign = bool(names) and not any(name.casefold() in owned for name in names)
            elif exists(target):
                result["error"] = "dir_is_file"
                return result
        except OSError:
            pass
        if is_root or str(target).rstrip("\\/").casefold() in protected or foreign:
            target = target / self.product.default_dir_name
            result["path"] = str(target)
            result["adjusted"] = True
        try:
            result["free"] = int(self.os.disk_free(target))
        except OSError:
            result["free"] = 0
        if result["free"] and result["free"] < int(result["required"]) + SPACE_MARGIN:
            result["error"] = "dir_no_space"
            return result
        result["ok"] = True
        return result

    def _shutdown_apps(self, app_dir: Path, report: Progress) -> None:
        for relative in self.product.shutdown:
            exe = app_dir / relative.replace("\\", os.sep)
            if exists(exe):
                report({"phase": "closing", "file": exe.name})
                try:
                    code = self.os.run_wait(exe, ["--shutdown"], timeout=20)
                    log.info("graceful shutdown %s -> %s", exe, code)
                except Exception as exc:  # noqa: BLE001 - an old build may not support it
                    log.warning("graceful shutdown of %s failed: %s", exe, exc)

    def close_apps(self, app_dir: Path, force: bool, report: Progress = lambda _e: None,
                   wait: Optional[float] = None) -> None:
        """Close programs running from *app_dir*; raise :class:`AppsRunning` if they stay."""
        if not exists(app_dir):
            return
        self._shutdown_apps(app_dir, report)
        deadline = time.monotonic() + (self.close_wait if wait is None else max(0.0, float(wait)))
        running = self.os.processes_in(app_dir)
        while running and time.monotonic() < deadline:
            self.os.sleep(0.25)
            running = self.os.processes_in(app_dir)
        if running and force:
            for pid, path in running:
                log.warning("terminating %s (pid %s)", path, pid)
                try:
                    self.os.terminate(pid)
                except OSError as exc:
                    log.warning("could not terminate %s: %s", pid, exc)
            for _ in range(20):
                running = self.os.processes_in(app_dir)
                if not running:
                    break
                self.os.sleep(0.25)
        if running:
            names = sorted({Path(path.replace("\\", "/")).name for _pid, path in running})
            raise AppsRunning(names)

        external = self.os.directory_users(app_dir)
        if external:
            names = sorted({f"{name} (PID {pid})" for pid, name in external})
            log.warning("installation directory held by external programs: %s", ", ".join(names))
            raise SetupError("directory_in_use", ", ".join(names), apps=names)

    def install(self, options: Options, report: Progress = lambda _e: None, force_close: bool = False) -> Dict[str, object]:
        if not self.payload:
            raise SetupError("no_payload", "this Setup has no payload")
        self.warnings = []
        product = self.product
        components, tasks = product.normalize(options.components, options.tasks)
        options = Options(install_dir=str(Path(options.install_dir)), components=components, tasks=tasks,
                          language=options.language if options.language in ("ru", "en") else "en",
                          launch=options.launch)
        app_dir = Path(options.install_dir)
        existing = self.detect()
        if existing.found and not same_path(existing.install_dir, str(app_dir)):
            log.info("previous installation in %s; installing to %s", existing.install_dir, app_dir)
        previous_state = self.read_state(app_dir) or {}
        previous_entries: Dict[str, List[str]] = {k: list(v) for k, v in dict(previous_state.get("entries") or {}).items()}
        total = self.payload.index.size_of(components)
        log.info("install %s %s to %s components=%s tasks=%s", product.id, self.version, app_dir, components, tasks)

        def emit(phase: str, percent: float, **extra: object) -> None:
            event = {"phase": phase, "percent": round(max(0.0, min(100.0, percent)), 2)}
            event.update(extra)
            report(event)

        emit("preparing", 1)
        try:
            free = self.os.disk_free(app_dir)
            if free and free < total + SPACE_MARGIN:
                raise SetupError("dir_no_space", f"not enough space: {free} < {total}",
                                 free=free, required=total)
        except OSError:
            pass
        self.close_apps(app_dir, force_close, lambda e: emit("closing", 3, **{k: v for k, v in e.items() if k != "phase"}))
        os.makedirs(long_path(app_dir), exist_ok=True)
        self.cleanup_leftovers(app_dir)

        token = secrets.token_hex(3)
        stage = app_dir / (STAGE_PREFIX + token)
        backup = app_dir / (BACKUP_PREFIX + token)
        os.makedirs(long_path(stage))
        swapped: List[Tuple[str, bool]] = []
        try:
            self._extract(stage, components, total, lambda pct, name, done: emit(
                "extracting", 5 + pct * 0.85, file=name, done=done, total=total))
            emit("finishing", 91)
            owned: Dict[str, List[str]] = {c: self.payload.index.top_entries(c) for c in components}
            os.makedirs(long_path(backup), exist_ok=True)
            for comp in components:
                for entry in owned[comp]:
                    target = app_dir / entry
                    had = exists(target)
                    if had:
                        retry(lambda t=target, e=entry: os.replace(long_path(t), long_path(backup / e)),
                              sleep=self.os.sleep)
                    swapped.append((entry, had))
                    retry(lambda e=entry: os.replace(long_path(stage / e), long_path(app_dir / e)),
                          sleep=self.os.sleep)
        except BaseException as exc:
            self._rollback(app_dir, backup, swapped)
            for leftover in (stage, backup):
                try:
                    if exists(leftover):
                        remove_entry(leftover)
                except OSError as cleanup_error:
                    log.warning("could not remove %s: %s", leftover, cleanup_error)
            if isinstance(exc, (SetupError, KeyboardInterrupt)):
                raise
            if isinstance(exc, PayloadError):
                raise SetupError("payload_damaged", str(exc)) from exc
            if isinstance(exc, OSError):
                raise SetupError("write_failed", f"{exc}", path=str(getattr(exc, "filename", "") or "")) from exc
            raise
        for leftover in (stage, backup):
            try:
                remove_entry(leftover)
            except OSError as exc:
                log.warning("could not remove %s now (cleaned on the next run): %s", leftover, exc)

        emit("registering", 94)

        for comp in product.components:
            keep = set(owned.get(comp.id, []))
            old = set(previous_entries.get(comp.id, []))
            if comp.id not in components:
                old |= {comp.dest} if comp.dest else set()
            for entry in sorted(old - keep):
                if not entry or entry in (STATE_FILE, LOG_FILE, product.uninstaller):
                    continue
                if any(entry in owned.get(other, []) for other in components):
                    continue
                try:
                    remove_entry(app_dir / entry)
                    log.info("removed %s (no longer installed)", entry)
                except OSError as exc:
                    self._warn(f"could not remove {entry}: {exc}")
        self._remove_legacy_layout(app_dir, components)
        self._apply_packages(app_dir, components, emit)
        self._write_uninstaller(app_dir)
        state = {
            "product": product.id, "version": self.version, "components": components, "tasks": tasks,
            "language": options.language, "entries": owned, "format": 1,
            "installed_at": _dt.datetime.now().isoformat(timespec="seconds"),
        }
        self._write_json(app_dir / STATE_FILE, state)
        self._apply_shortcuts(app_dir, components, tasks, options.language)
        self._refresh_icons(app_dir, components, options.language)
        self._apply_run_values(app_dir, components, tasks)
        path_changed = self._apply_path(app_dir, components)
        self._apply_protocols(app_dir, components)
        self._register(app_dir, components, tasks, options.language, total)
        if path_changed:
            try:
                self.os.broadcast_environment()
            except OSError as exc:
                self._warn(f"environment broadcast failed: {exc}")
        webview = ""
        try:
            webview = self.os.webview2_version()
        except OSError:
            pass
        if not webview and "memuai" in components:
            self._warn("Microsoft Edge WebView2 Runtime was not found; MemuAI needs it to open its window")
        emit("done", 100)
        log.info("install finished: %s", app_dir)
        return {"ok": True, "install_dir": str(app_dir), "version": self.version, "components": components,
                "tasks": tasks, "warnings": list(self.warnings), "webview2": webview}

    def _apply_packages(self, app_dir: Path, components: List[str],
                        emit: Callable[..., None] = lambda *_a, **_k: None) -> None:
        """Round 29: optional components that were not selected are left as a zip in the install
        dir (``Packages\\MemuNote.zip``) so the app installs them later without the Setup; a
        selected component's stale zip is removed. Failures are warnings, never fatal."""
        if not self.payload:
            return
        for comp_id, relative in self.product.packages:
            target = app_dir / relative.replace("\\", os.sep)
            try:
                if comp_id in components:
                    if exists(target):
                        remove_entry(target)
                    continue
                if comp_id not in self.payload.index.components():
                    continue

                if comp_id not in self.product.normalize(list(components) + [comp_id], [])[0]:
                    if exists(target):
                        remove_entry(target)
                    continue
                emit("packaging", 93, file=target.name)
                self._write_package(target, self.product.component(comp_id))
                log.info("package %s written (%s not selected)", target, comp_id)
            except (OSError, PayloadError, zipfile.BadZipFile) as exc:
                self._warn(f"package {relative}: {exc}")

    def _write_package(self, target: Path, comp: Component) -> None:
        assert self.payload is not None
        prefix = (comp.dest.replace("\\", "/").strip("/") + "/") if comp.dest else ""
        os.makedirs(long_path(target.parent), exist_ok=True)
        partial = target.with_name(target.name + ".partial")
        try:
            with zipfile.ZipFile(long_path(partial), "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6,
                                 allowZip64=True) as archive:

                archive.writestr(f"{comp.id}-package.json", json.dumps(
                    {"component": comp.id, "name": comp.dest or comp.id, "version": self.version,
                     "exe": Path(comp.exe.replace("\\", "/")).name, "format": 1}, ensure_ascii=False))
                for comp_id, rel in self.payload.index.dirs:
                    if comp_id == comp.id and rel.startswith(prefix) and rel != prefix.rstrip("/"):
                        archive.writestr(rel[len(prefix):].rstrip("/") + "/", b"")
                for entry, chunks in self.payload.iter_files([comp.id], self.cancel_event.is_set):
                    name = entry.path[len(prefix):] if prefix and entry.path.startswith(prefix) else entry.path
                    with archive.open(name, "w", force_zip64=True) as handle:
                        for piece in chunks:
                            handle.write(piece)
                    if self.cancel_event.is_set():
                        raise Cancelled()
            os.replace(long_path(partial), long_path(target))
        finally:
            if exists(partial):
                try:
                    os.remove(long_path(partial))
                except OSError:
                    pass

    def _warn(self, text: str) -> None:
        log.warning(text)
        self.warnings.append(text)

    def _extract(self, stage: Path, components: List[str], total: int,
                 progress: Callable[[float, str, int], None]) -> None:
        assert self.payload is not None
        done = 0
        last = 0.0
        made: set = set()
        for comp, rel in self.payload.index.dirs:
            if comp in components:
                os.makedirs(long_path(stage / rel), exist_ok=True)
        for entry, chunks in self.payload.iter_files(components, self.cancel_event.is_set):
            target = stage / entry.path
            parent = os.path.dirname(long_path(target))
            if parent not in made:
                os.makedirs(parent, exist_ok=True)
                made.add(parent)
            with open(long_path(target), "wb") as handle:
                for piece in chunks:
                    handle.write(piece)
                    done += len(piece)
                    now = time.monotonic()
                    if now - last > 0.05:
                        last = now
                        progress(100.0 * done / max(total, 1), entry.path, done)
                    if self.cancel_event.is_set():
                        break
            if self.cancel_event.is_set():
                raise Cancelled()
            progress(100.0 * done / max(total, 1), entry.path, done)
        if self.cancel_event.is_set():
            raise Cancelled()
        if done != total:
            raise SetupError("payload_damaged", f"extracted {done} of {total} bytes")

    def _rollback(self, app_dir: Path, backup: Path, swapped: List[Tuple[str, bool]]) -> None:
        for entry, had in reversed(swapped):
            try:
                if exists(app_dir / entry):
                    remove_entry(app_dir / entry)
                if had and exists(backup / entry):
                    os.replace(long_path(backup / entry), long_path(app_dir / entry))
                log.info("rolled back %s", entry)
            except OSError as exc:
                log.error("rollback of %s failed: %s", entry, exc)

    def cleanup_leftovers(self, app_dir: Path) -> None:
        try:
            names = os.listdir(long_path(app_dir))
        except OSError:
            return
        for name in names:
            if name.startswith(STAGE_PREFIX) or name.startswith(BACKUP_PREFIX):
                try:
                    remove_entry(app_dir / name)
                except OSError as exc:
                    log.warning("leftover %s stays: %s", name, exc)

    def _remove_legacy_layout(self, app_dir: Path, components: List[str]) -> None:
        """Old flat layout (one shared _internal) and Inno's own uninstaller files."""
        proxy = self.product.component("proxy")
        if self.product.legacy_files and exists(app_dir / proxy.exe.replace("\\", os.sep)):
            for name in self.product.legacy_files:
                if any(name in self.payload.index.top_entries(c) for c in components):
                    continue
                if exists(app_dir / name):
                    try:
                        remove_entry(app_dir / name)
                        log.info("removed legacy %s", name)
                    except OSError as exc:
                        self._warn(f"could not remove legacy {name}: {exc}")
        try:
            names = os.listdir(long_path(app_dir))
        except OSError:
            names = []
        for name in names:
            if re.fullmatch(r"unins\d{3}\.(exe|dat|msg)", name, flags=re.I):
                try:
                    remove_entry(app_dir / name)
                    log.info("removed Inno uninstaller file %s", name)
                except OSError as exc:
                    self._warn(f"could not remove {name}: {exc}")

    def _write_uninstaller(self, app_dir: Path) -> None:
        source = self.stub_source or (self.payload.path if self.payload else None)
        if source is None:
            return
        if self.payload and Path(source) == self.payload.path:
            stub = self.payload.read_stub()
        else:
            stub = Path(source).read_bytes()
        stub = strip_signature_directory(stub)
        target = app_dir / self.product.uninstaller
        temporary = app_dir / (self.product.uninstaller + ".new")
        with open(long_path(temporary), "wb") as handle:
            handle.write(stub)
        retry(lambda: os.replace(long_path(temporary), long_path(target)), sleep=self.os.sleep)

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        temporary = path.with_name(path.name + ".new")
        with open(long_path(temporary), "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(long_path(temporary), long_path(path))

    def shortcut_name(self, name: str, language: str) -> str:
        return i18n.tr(name[1:], language) if name.startswith("@") else name

    def _shortcut_paths(self, shortcut, language: str) -> List[Path]:
        folder = self.os.known_folder(shortcut.where)
        if shortcut.where == "programs":
            folder = folder / self.product.group
        names = [self.shortcut_name(shortcut.name, language)]
        if shortcut.name.startswith("@"):
            names += [self.shortcut_name(shortcut.name, other) for other in ("ru", "en") if other != language]
        return [folder / f"{name}.lnk" for name in names]

    def _apply_shortcuts(self, app_dir: Path, components: List[str], tasks: List[str], language: str) -> None:
        for where, name in self.product.legacy_shortcuts:
            try:
                folder = self.os.known_folder(where)
                if where == "programs":
                    folder = folder / self.product.group
                link = folder / f"{name}.lnk"
                if exists(link):
                    os.remove(long_path(link))
            except OSError as exc:
                log.warning("legacy shortcut %s: %s", name, exc)
        for shortcut in self.product.shortcuts:
            try:
                paths = self._shortcut_paths(shortcut, language)
            except OSError as exc:
                self._warn(f"shortcut folder unavailable: {exc}")
                continue
            wanted = shortcut.component in components and (not shortcut.task or shortcut.task in tasks)

            for stale in paths[1:]:
                if exists(stale):
                    try:
                        os.remove(long_path(stale))
                    except OSError:
                        pass
            link = paths[0]
            if not wanted:
                if exists(link):
                    try:
                        os.remove(long_path(link))
                    except OSError as exc:
                        self._warn(f"could not remove shortcut {link.name}: {exc}")
                continue
            target = app_dir / shortcut.target.replace("\\", os.sep)
            workdir = os.path.expandvars(shortcut.workdir) if shortcut.workdir else str(target.parent)
            if shortcut.workdir == "%USERPROFILE%":
                workdir = self.os.env("USERPROFILE") or workdir
            try:
                os.makedirs(long_path(link.parent), exist_ok=True)
                self.os.create_shortcut(link, target, shortcut.args, workdir, str(target), "", shortcut.app_id)
            except OSError as exc:
                self._warn(f"could not create shortcut {link.name}: {exc}")

        try:
            group = self.os.known_folder("programs") / self.product.group
            if exists(group) and not os.listdir(long_path(group)):
                os.rmdir(long_path(group))
        except OSError:
            pass

    def _refresh_icons(self, app_dir: Path, components: List[str], language: str) -> None:
        """Round 33 (#16): an update may change the exe icons; Explorer keeps the old ones
        cached for the shortcuts (or shows a white page) until told the files changed."""
        paths: List[Path] = []
        for comp in self.product.components:
            if comp.id in components and comp.exe:
                paths.append(app_dir / comp.exe.replace("\\", os.sep))
        for shortcut in self.product.shortcuts:
            try:
                paths += [p for p in self._shortcut_paths(shortcut, language)[:1] if exists(p)]
            except OSError:
                continue
        try:
            self.os.refresh_shell_icons(paths)
        except Exception as exc:  # noqa: BLE001
            log.warning("icon refresh failed: %s", exc)

    def _apply_run_values(self, app_dir: Path, components: List[str], tasks: List[str]) -> None:
        for run in self.product.run_values:
            try:
                if run.component in components and run.task in tasks:
                    exe = app_dir / run.target.replace("\\", os.sep)
                    value = f'"{exe}"' + (f" {run.args}" if run.args else "")
                    self.os.reg_set(HKCU, RUN_KEY, run.name, value, "sz")
                else:
                    self.os.reg_delete_value(HKCU, RUN_KEY, run.name)
            except FileNotFoundError:
                pass
            except OSError as exc:
                self._warn(f"startup entry {run.name}: {exc}")

    def _user_path(self) -> str:
        try:
            return str(self.os.reg_get(HKCU, "Environment", "Path") or "")
        except OSError:
            return ""

    def _apply_path(self, app_dir: Path, components: Sequence[str], removing: bool = False) -> bool:
        changed = False
        for comp, folder in self.product.path_entries:
            entry = str(app_dir / folder)
            current = self._user_path()
            wanted = (not removing) and comp in components
            updated = add_path_entry(current, entry) if wanted else remove_path_entry(current, entry)
            if updated != current:
                try:
                    if updated:
                        self.os.reg_set(HKCU, "Environment", "Path", updated, "expand_sz")
                    else:
                        self.os.reg_delete_value(HKCU, "Environment", "Path")
                    changed = True
                except OSError as exc:
                    self._warn(f"PATH update failed: {exc}")
        return changed

    def _apply_protocols(self, app_dir: Path, components: Sequence[str], removing: bool = False) -> None:
        """Round 35 (protocol): ``memuai://`` → ``"<MemuAI.exe>" --deeplink "%1"`` under HKCU\\Software\\Classes.

        Removed on uninstall (or when the component is deselected) only when the handler is ours: it points
        into this install folder, so a second copy of MemuAI elsewhere keeps its registration."""
        for comp, scheme, relative, description in self.product.url_protocols:
            base = "Software\\Classes\\" + scheme
            exe = app_dir / relative.replace("\\", os.sep)
            command_key = base + "\\shell\\open\\command"
            try:
                if not removing and comp in components:
                    self.os.reg_set(HKCU, base, "", description, "sz")
                    self.os.reg_set(HKCU, base, "URL Protocol", "", "sz")
                    self.os.reg_set(HKCU, base + "\\DefaultIcon", "", f'"{exe}",0', "sz")
                    self.os.reg_set(HKCU, command_key, "", f'"{exe}" --deeplink "%1"', "sz")
                    continue
                current = str(self.os.reg_get(HKCU, command_key, "") or "")
                if current and str(exe).lower() not in current.lower():
                    continue
                for sub in ("\\shell\\open\\command", "\\shell\\open", "\\shell", "\\DefaultIcon", ""):
                    try:
                        self.os.reg_delete_key(HKCU, base + sub)
                    except FileNotFoundError:
                        pass
            except OSError as exc:
                self._warn(f"{scheme}:// registration: {exc}")

    def _register(self, app_dir: Path, components: List[str], tasks: List[str], language: str, size: int) -> None:
        product = self.product
        icon = ""
        for candidate in product.icons:
            path = app_dir / candidate.replace("\\", os.sep)
            if exists(path):
                icon = f"{path},0"
                break
        uninstaller = app_dir / product.uninstaller
        values = {
            "DisplayName": product.name,
            "DisplayVersion": self.version,
            "Publisher": product.publisher,
            "DisplayIcon": icon or str(uninstaller),
            "UninstallString": f'"{uninstaller}" --uninstall',
            "QuietUninstallString": f'"{uninstaller}" --uninstall --silent',
            "InstallLocation": str(app_dir) + os.sep,
            "Comments": product.comments.get(language) or product.comments.get("en", ""),
            "InstallDate": _dt.date.today().strftime("%Y%m%d"),
            "MemuAI: Components": ",".join(components),
            "MemuAI: Tasks": ",".join(tasks),
            "MemuAI: Language": language,
        }
        try:

            try:
                self.os.reg_delete_key(HKCU, self.uninstall_key)
            except FileNotFoundError:
                pass
            for name, value in values.items():
                self.os.reg_set(HKCU, self.uninstall_key, name, value, "sz")
            self.os.reg_set(HKCU, self.uninstall_key, "EstimatedSize", max(1, size // 1024), "dword")
            self.os.reg_set(HKCU, self.uninstall_key, "NoModify", 1, "dword")
            self.os.reg_set(HKCU, self.uninstall_key, "NoRepair", 1, "dword")
        except OSError as exc:
            self._warn(f"could not register in Installed apps: {exc}")

    def launch(self, app_dir: Path, components: Sequence[str]) -> bool:
        for comp, exe, args in self.product.launch:
            if comp in components:
                path = Path(app_dir) / exe.replace("\\", os.sep)
                if exists(path):
                    try:
                        self.os.launch(path, [args] if args else [], path.parent)
                        return True
                    except OSError as exc:
                        self._warn(f"could not start {path.name}: {exc}")
                        return False
        return False

    def data_dirs(self) -> List[Path]:
        result = []
        for env_name, folder in self.product.data_dirs:
            base = self.os.env(env_name)
            if base:
                result.append(Path(base) / folder)
        return result

    def uninstall(self, app_dir: Path, remove_data: bool = False, report: Progress = lambda _e: None,
                  force_close: bool = False) -> Dict[str, object]:
        product = self.product
        self.warnings = []
        app_dir = Path(app_dir)
        state = self.read_state(app_dir) or {}
        log.info("uninstall %s from %s (remove data: %s)", product.id, app_dir, remove_data)

        def emit(phase: str, percent: float, **extra: object) -> None:
            event = {"phase": phase, "percent": percent}
            event.update(extra)
            report(event)

        emit("closing", 4)
        self.close_apps(app_dir, force_close, lambda e: emit("closing", 6, **{k: v for k, v in e.items() if k != "phase"}))
        language = str(state.get("language") or "en")
        emit("shortcuts", 15)
        for shortcut in product.shortcuts:
            try:
                for link in self._shortcut_paths(shortcut, language):
                    if exists(link):
                        os.remove(long_path(link))
            except OSError as exc:
                self._warn(f"shortcut: {exc}")
        for where, name in product.legacy_shortcuts:
            try:
                folder = self.os.known_folder(where)
                folder = folder / product.group if where == "programs" else folder
                if exists(folder / f"{name}.lnk"):
                    os.remove(long_path(folder / f"{name}.lnk"))
            except OSError:
                pass
        try:
            group = self.os.known_folder("programs") / product.group
            if exists(group) and not os.listdir(long_path(group)):
                os.rmdir(long_path(group))
        except OSError:
            pass
        for run in product.run_values:

            try:
                self.os.reg_delete_value(HKCU, RUN_KEY, run.name)
            except OSError:
                pass
        path_changed = self._apply_path(app_dir, [], removing=True)
        self._apply_protocols(app_dir, [], removing=True)
        for _comp, relative, args in product.pre_uninstall:

            exe = app_dir / relative.replace("\\", os.sep)
            if exists(exe):
                try:
                    code = self.os.run_wait(exe, shlex.split(args), timeout=20)
                    log.info("pre-uninstall %s %s -> %s", exe, args, code)
                except Exception as exc:  # noqa: BLE001 - never block the uninstall
                    self._warn(f"pre-uninstall {exe.name}: {exc}")

        emit("files", 25)
        entries: List[str] = []
        for comp in product.components:
            for entry in dict(state.get("entries") or {}).get(comp.id, []) or ([comp.dest] if comp.dest else []):
                if entry not in entries:
                    entries.append(entry)
        if not any(c.dest for c in product.components) and not state.get("entries"):
            entries += ["MemuAIProxy.exe", "MemuAIProxy.pkg", "_internal"]
        entries += [name for name in product.legacy_files if name not in entries]
        for _comp, relative in product.packages:
            head = relative.replace("\\", "/").split("/", 1)[0]
            if head and head not in entries:
                entries.append(head)
        entries += [product.uninstaller, STATE_FILE, LOG_FILE, product.uninstaller + ".new"]
        try:
            for name in os.listdir(long_path(app_dir)):
                if (re.fullmatch(r"unins\d{3}\.(exe|dat|msg)", name, flags=re.I)
                        or name.startswith(STAGE_PREFIX) or name.startswith(BACKUP_PREFIX)):
                    entries.append(name)
        except OSError:
            pass
        failed: List[str] = []
        for number, entry in enumerate(entries):
            target = app_dir / entry
            if not exists(target):
                continue
            emit("files", 25 + 60 * number / max(len(entries), 1), file=entry)
            try:
                retry(lambda t=target: remove_entry(t), attempts=6, sleep=self.os.sleep)
            except OSError as exc:
                failed.append(entry)
                self._warn(f"could not remove {target}: {exc}")
        try:
            if exists(app_dir) and not os.listdir(long_path(app_dir)):
                os.rmdir(long_path(app_dir))
        except OSError:
            pass
        emit("registry", 88)
        try:
            self.os.reg_delete_key(HKCU, self.uninstall_key)
        except OSError:
            pass
        removed_data: List[str] = []
        if remove_data:
            emit("data", 92)
            for folder in self.data_dirs():
                if self._safe_data_dir(folder, app_dir) and exists(folder):
                    try:
                        retry(lambda f=folder: remove_entry(f), attempts=6, sleep=self.os.sleep)
                        removed_data.append(str(folder))
                    except OSError as exc:
                        self._warn(f"could not remove data folder {folder}: {exc}")
        if path_changed:
            try:
                self.os.broadcast_environment()
            except OSError:
                pass
        emit("done", 100)
        return {"ok": not failed, "failed": failed, "removed_data": removed_data,
                "kept_data": [str(f) for f in self.data_dirs() if exists(f)], "warnings": list(self.warnings)}

    def _safe_data_dir(self, folder: Path, app_dir: Path) -> bool:
        """Only the exact known data folders directly inside APPDATA/LOCALAPPDATA."""
        allowed = {(self.os.env(env).rstrip("\\/").casefold(), name.casefold())
                   for env, name in self.product.data_dirs if self.os.env(env)}
        key = (str(folder.parent).rstrip("\\/").casefold(), folder.name.casefold())
        if key not in allowed or not folder.name:
            return False
        app = str(app_dir).rstrip("\\/").casefold()
        data = str(folder).rstrip("\\/").casefold()

        return not (app == data or data.startswith(app + os.sep) or data.startswith(app + "\\"))

def options_to_dict(options: Options) -> dict:
    return asdict(options)
