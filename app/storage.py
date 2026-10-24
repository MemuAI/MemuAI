"""Надёжное хранение JSON-состояния: атомарная запись, кольцевые бэкапы,
автовосстановление при повреждении файла.

Любая потеря данных — худший сценарий для 2api-прокси (куки собираются руками),
поэтому каждое сохранение сначала копирует предыдущую версию в data/backups/,
а при чтении битого файла состояние восстанавливается из свежего бэкапа.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import logging
import os
import shutil
import threading
import time
from typing import Any, Callable, Iterator, List, Union

PathValue = Union[str, os.PathLike[str]]

BACKUP_KEEP = 10
_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}
_TX_LOCAL = threading.local()
log = logging.getLogger("memuai.storage")


def _path_key(path: PathValue) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))


def _path_lock(path: PathValue) -> threading.RLock:
    key = _path_key(path)
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


def _backups_dir(path: PathValue) -> str:
    path = os.fspath(path)
    return os.path.join(os.path.dirname(path), "backups")


@contextmanager
def _windows_mutex(key: str) -> Iterator[None]:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateMutexW
    create.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    create.restype = wintypes.HANDLE
    wait = kernel32.WaitForSingleObject
    wait.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait.restype = wintypes.DWORD
    release = kernel32.ReleaseMutex
    release.argtypes = (wintypes.HANDLE,)
    release.restype = wintypes.BOOL
    close = kernel32.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL

    digest = hashlib.sha256(key.encode("utf-8", "surrogatepass")).hexdigest()
    handle = create(None, False, rf"Local\MemuAI.JSON.{digest}")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        result = wait(handle, 0xFFFFFFFF)
        if result not in (0x00000000, 0x00000080):  # WAIT_OBJECT_0 / WAIT_ABANDONED
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = True
        yield
    finally:
        if acquired:
            release(handle)
        close(handle)


@contextmanager
def _posix_file_lock(key: str) -> Iterator[None]:
    import fcntl
    lock_path = key + ".lock"
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    handle = open(lock_path, "a+b")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


@contextmanager
def _interprocess_lock(key: str) -> Iterator[None]:
    """Serialize one JSON transaction across Proxy and MemuAI processes."""
    if os.name == "nt":
        with _windows_mutex(key):
            yield
    else:
        with _posix_file_lock(key):
            yield


@contextmanager
def _json_transaction(path: PathValue) -> Iterator[None]:
    key = _path_key(path)
    with _path_lock(key):
        held = getattr(_TX_LOCAL, "held", None)
        if held is None:
            held = _TX_LOCAL.held = set()
        if key in held:
            yield
            return
        with _interprocess_lock(key):
            held.add(key)
            try:
                yield
            finally:
                held.remove(key)


def _after_fork() -> None:
    global _LOCKS_GUARD, _PATH_LOCKS, _TX_LOCAL
    _LOCKS_GUARD = threading.Lock()
    _PATH_LOCKS = {}
    _TX_LOCAL = threading.local()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def _stem(path: PathValue) -> str:
    path = os.fspath(path)
    return os.path.splitext(os.path.basename(path))[0]


def _oldest_first(bdir: str, files: List[str]) -> List[str]:
    """Backups from oldest to newest.

    The file name carries a *local* wall-clock stamp, which repeats an hour
    when DST ends: sorting by name alone then treats the newest backups as
    the oldest, so pruning deleted them and recovery picked a stale copy.
    The modification time (copied from the just-written main file) is
    monotonic; the name only breaks ties.
    """
    def key(name: str) -> tuple:
        try:
            return (os.stat(os.path.join(bdir, name)).st_mtime_ns, name)
        except OSError:
            return (0, name)
    return sorted(files, key=key)


def _backup_candidates(path: PathValue) -> List[str]:
    path = os.fspath(path)
    bdir = _backups_dir(path)
    if not os.path.isdir(bdir):
        return []
    stem = _stem(path)
    try:
        files = [f for f in os.listdir(bdir) if f.startswith(stem + "-") and f.endswith(".json")]
    except OSError:
        return []
    return [os.path.join(bdir, f) for f in reversed(_oldest_first(bdir, files))]


def _prune_backups(path: PathValue) -> None:
    path = os.fspath(path)
    bdir = _backups_dir(path)
    stem = _stem(path)
    try:
        files = [f for f in os.listdir(bdir) if f.startswith(stem + "-") and f.endswith(".json")]
    except OSError:
        return
    for f in _oldest_first(bdir, files)[:-BACKUP_KEEP]:
        try:
            os.remove(os.path.join(bdir, f))
        except OSError:
            pass


_PRETTY_JSON_LIMIT = 256 * 1024


def _write_unlocked(path: PathValue, data: Any) -> None:
    path = os.fspath(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
    try:
        # ``indent`` forces json's pure-Python encoder, which holds the GIL
        # for ~0.3 s on a multi-megabyte chats.json and stalls the Proxy's
        # event loop (Telegram bot included). Encode compactly with the C
        # encoder; only small files are re-encoded pretty for readability.
        text = json.dumps(data, ensure_ascii=False)
        if len(text) <= _PRETTY_JSON_LIMIT:
            text = json.dumps(data, ensure_ascii=False, indent=2)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(8):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 7:
                    raise
                # Virus scanners and indexers can briefly open a just-written
                # JSON file without delete sharing. Keep atomic replacement,
                # but tolerate that short external sharing window.
                time.sleep(min(0.01 * (2 ** attempt), 0.1))
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _quarantine_corrupt(path: str) -> None:
    """Keep an unreadable main file aside before anything overwrites it.

    chats.json / preferences.json are written without backups, so a damaged
    main file used to be replaced by the default plus the next change and was
    lost for good. The copy is named by size+mtime, so repeated reads of the
    same damaged file do not multiply it, and it never matches the
    ``<stem>-*.json`` backup pattern (it is not offered for recovery).
    """
    try:
        stat = os.stat(path)
        if not stat.st_size:
            return
        bdir = _backups_dir(path)
        dst = os.path.join(bdir, f"{_stem(path)}.corrupt-{stat.st_mtime_ns}-{stat.st_size}.json")
        if os.path.exists(dst):
            return
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(path, dst)
        log.warning("JSON %s is damaged; a copy was kept as %s", path, dst)
    except OSError:
        pass


def _load_primary(path: str) -> Any:
    """json.load of the main file, tolerating a short antivirus sharing lock."""
    for attempt in range(8):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except PermissionError:
            if os.name != "nt" or attempt == 7:
                raise
            time.sleep(min(0.01 * (2 ** attempt), 0.1))


def _read_unlocked(path: PathValue, default: Any,
                   validator: Callable[[Any], bool] | None = None, *,
                   strict: bool = False) -> Any:
    """``strict`` (read-modify-write): a main file that exists but cannot be
    *opened* (sharing violation, permissions) raises instead of silently
    falling back to the default — otherwise the caller would overwrite the
    whole intact file (all chats) with the default plus its one change."""
    path = os.fspath(path)
    for candidate in [path] + _backup_candidates(path):
        try:
            if candidate == path:
                data = _load_primary(candidate)
            else:
                with open(candidate, "r", encoding="utf-8") as f:
                    data = json.load(f)
            if validator is not None and not validator(data):
                if candidate == path:
                    _quarantine_corrupt(path)
                continue
        except FileNotFoundError:
            continue
        except OSError:
            if strict and candidate == path:
                raise
            continue
        except Exception:
            if candidate == path:
                _quarantine_corrupt(path)
            continue
        if candidate != path:
            try:
                _write_unlocked(path, data)
            except OSError as exc:
                # A readable backup remains useful even when antivirus or a
                # read-only directory prevents immediately repairing main.
                log.warning("JSON recovered from %s but main was not repaired: %s", candidate, exc)
        return data
    return default


def _backup_unlocked(path: PathValue) -> None:
    path = os.fspath(path)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000_000:09d}"
    dst = os.path.join(_backups_dir(path), f"{_stem(path)}-{stamp}.json")
    try:
        os.makedirs(_backups_dir(path), exist_ok=True)
        shutil.copy2(path, dst)
        _prune_backups(path)
    except OSError:
        pass


def save_json(path: PathValue, data: Any, *, backup: bool = True) -> None:
    """Атомарно пишет JSON и копирует результат в data/backups/.

    Бэкап создаётся ПОСЛЕ успешной записи: свежий бэкап всегда равен
    последнему рабочему состоянию (при сбое теряется максимум текущая
    операция), а более старые копии в каталоге дают историю версий.
    """
    path = os.fspath(path)
    with _json_transaction(path):
        _write_unlocked(path, data)
        if backup:
            _backup_unlocked(path)


def load_json_with_recovery(path: PathValue, default: Any, *,
                            validator: Callable[[Any], bool] | None = None) -> Any:
    """Читает JSON; при повреждении основного файла пробует свежие бэкапы
    и восстанавливает ими основной файл."""
    path = os.fspath(path)
    with _json_transaction(path):
        return _read_unlocked(path, default, validator)


_UNSET = object()


def mutate_json(path: PathValue, default: Any,
                mutator: Callable[[Any], bool | tuple[bool, Any]], *,
                backup: bool = True, validator: Callable[[Any], bool] | None = None,
                repair_invalid: bool = False) -> Any:
    """Run one atomic cross-process read-modify-write transaction.

    ``mutator`` edits the loaded value in place and returns whether it needs to
    be persisted. It may instead return ``(changed, replacement)`` when the
    root JSON type itself needs repair. The resulting value is always returned.
    """
    path = os.fspath(path)
    with _json_transaction(path):
        needs_repair = False
        data: Any = _UNSET
        if repair_invalid:
            try:
                primary = _load_primary(path)
                needs_repair = validator is not None and not validator(primary)
                if not needs_repair:
                    data = primary  # already parsed: don't read the file twice
            except FileNotFoundError:
                needs_repair = True
            except OSError:
                pass  # locked/unreadable: the strict read below raises instead of overwriting
            except Exception:
                needs_repair = True
        if data is _UNSET:
            data = _read_unlocked(path, copy.deepcopy(default), validator, strict=True)
        outcome = mutator(data)
        if isinstance(outcome, tuple):
            changed, data = outcome
        else:
            changed = outcome
        if changed or needs_repair:
            _write_unlocked(path, data)
            if backup:
                _backup_unlocked(path)
        return data
