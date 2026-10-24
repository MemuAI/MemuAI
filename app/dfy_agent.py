"""MemuAI Remote Agent — один файл, только стандартная библиотека.

Запускается на удалённой машине (Linux в первую очередь, работает и на
Windows/macOS) и даёт DFY полный доступ к среде: команды оболочки, файлы,
пакеты, процессы, сведения о системе.  Тот же модуль MemuAI импортирует у
себя (``app.dfy_agent``) для режима «Этот компьютер», поэтому проверки
безопасности одни и те же локально и удалённо.

    python3 memuai_remote_agent.py serve       # печатает код подключения MEMU1-…
    python3 memuai_remote_agent.py serve --bind 0.0.0.0 --port 8765 --root /srv
    ssh -L 8765:127.0.0.1:8765 user@server        # машина за NAT: агент на 127.0.0.1 + SSH-туннель
    python3 memuai_remote_agent.py systemd            # юнит для systemd

Безопасность:
* MemuAI подключается к агенту сам (или запускает его по SSH без установки);
* запросы подписаны HMAC-SHA256 от токена (метка времени + nonce: подпись
  нельзя переиграть); токен по сети не передаётся. TLS — ``--tls-cert/--tls-key``;
  без него держите агента за SSH-туннелем или в доверенной сети;
* агент НИКОГДА не трогает сам себя: свой файл, свой каталог состояния, папку
  MemuAI и её данные — ни удалить, ни перезаписать, ни переместить, ни убить
  свой процесс/службу.  Проверка идёт по реальному пути после ``resolve()``
  (симлинки раскрываются), и по обе стороны: нельзя удалить и родителя;
* опасные команды (``rm -rf``, форматирование, выключение, удаление
  пользователей) требуют подтверждения пользователя — агент выполнит их только
  с ``confirmed: true``, а катастрофические (``rm -rf /``, ``mkfs`` системного
  диска, форк-бомба) не выполняет вовсе;
* необязательный белый список корней (``--root``) ограничивает файловые
  операции указанными каталогами;
* таймауты, лимит размера вывода, потоковая отдача и отмена задания.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

AGENT_NAME = "MemuAI Remote Agent"
AGENT_VERSION = "1.0"
PROTOCOL = 1
DEFAULT_PORT = 8765
MAX_OUTPUT = 200_000          # байт вывода одной команды, дальше — обрезка
MAX_READ = 400_000            # байт при чтении файла
MAX_LIST = 400                # записей в листинге каталога
DEFAULT_TIMEOUT = 120.0
MAX_TIMEOUT = 3600.0
AUTH_WINDOW = 300             # допустимый сдвиг часов, с
WINDOWS = os.name == "nt"


class Refused(Exception):
    """Отказ по правилам безопасности (ответ 403 с человеческим текстом)."""

    def __init__(self, message: str, message_en: str = "", *, level: str = "blocked") -> None:
        super().__init__(message)
        self.message_en = message_en or message
        self.level = level


class NeedsConfirm(Refused):
    """Действие опасно: нужно подтверждение пользователя (``confirmed: true``)."""

    def __init__(self, message: str, message_en: str = "") -> None:
        super().__init__(message, message_en, level="confirm")


# --------------------------------------------------------------- защита ---
# Пути, которые нельзя удалить/перезаписать/переместить ни при каких условиях:
# сам агент, его состояние и MemuAI с его данными. Задаются конфигурацией
# (MemuAI присылает свои пути при подключении) плюс вычисляются из __file__.
_SYSTEM_ROOTS_POSIX = ("/", "/bin", "/sbin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/proc",
                       "/root", "/run", "/sbin", "/sys", "/usr", "/usr/bin", "/usr/lib", "/usr/sbin",
                       "/var", "/var/lib", "/home", "/Users", "/Applications", "/System", "/Library")
_SYSTEM_ROOTS_WIN = (r"C:\\", r"C:\Windows", r"C:\Windows\System32", r"C:\Program Files",
                     r"C:\Program Files (x86)", r"C:\Users")

# Команды, которые не выполняются никогда (разрушают машину целиком).
_FATAL_PATTERNS: Tuple[Tuple[str, str, str], ...] = (
    (r"(?:^|[;&|]\s*)(?:sudo\s+)?rm\s+(?:-[a-zA-Z]*\s+)*-?[a-zA-Z]*[rR][a-zA-Z]*f?[a-zA-Z]*\s+(?:--no-preserve-root\s+)?/\s*(?:$|[;&|])",
     "rm -rf / стирает всю машину", "rm -rf / wipes the whole machine"),
    (r"(?:^|[;&|]\s*)(?:sudo\s+)?rm\s+(?:-\S+\s+)*--no-preserve-root",
     "rm --no-preserve-root стирает корень", "rm --no-preserve-root wipes the root"),
    (r":\(\)\s*\{\s*:\s*\|\s*:?\s*&?\s*\}\s*;?\s*:", "форк-бомба", "a fork bomb"),
    (r"\bmkfs(\.\w+)?\s+[^|;&]*/dev/(sd|nvme|vd|hd|xvd|mmcblk)\w*", "форматирование диска",
     "formatting a disk"),
    (r"\bdd\b[^|;&]*\bof=/dev/(sd|nvme|vd|hd|xvd|mmcblk)\w*", "запись поверх диска через dd",
     "dd writing over a raw disk"),
    (r"(?i)\bformat\s+[a-z]:\s*", "форматирование диска", "formatting a disk"),
    (r"(?i)>\s*/dev/(sd|nvme|vd|hd)\w*", "запись поверх диска", "writing over a raw disk"),
)

# Команды, которые выполняются только после подтверждения пользователя.
_DANGER_PATTERNS: Tuple[Tuple[str, str, str], ...] = (
    (r"(?:^|[;&|]\s*)(?:sudo\s+)?rm\s+(?:-\S+\s+)*-\S*[rR]\S*\s", "рекурсивное удаление (rm -r)",
     "a recursive delete (rm -r)"),
    (r"(?:^|[;&|]\s*)(?:sudo\s+)?rm\s+(?:-\S+\s+)*-\S*f", "принудительное удаление (rm -f)",
     "a forced delete (rm -f)"),
    (r"(?i)\b(shutdown|reboot|halt|poweroff|init\s+0|init\s+6)\b", "выключение или перезагрузку машины",
     "shutting the machine down or rebooting it"),
    (r"(?i)\bRemove-Item\b[^|;&]*-Recurse", "рекурсивное удаление (Remove-Item -Recurse)",
     "a recursive delete (Remove-Item -Recurse)"),
    (r"(?i)\brmdir\s+/s\b", "рекурсивное удаление каталога", "a recursive directory delete"),
    (r"(?i)\b(userdel|deluser|net\s+user\s+\S+\s+/delete)\b", "удаление пользователя",
     "deleting a user account"),
    (r"(?i)\b(mkfs|fdisk|parted|diskpart|wipefs|shred)\b", "операцию с разделами диска",
     "a disk partition operation"),
    (r"(?i)\bchmod\s+(-[a-zA-Z]+\s+)*777\s+/\s*$", "смену прав на корень", "changing the permissions of /"),
    (r"(?i)\b(iptables\s+-F|ufw\s+disable|systemctl\s+(stop|disable)\s+(ssh|sshd))\b",
     "отключение доступа к серверу (сеть или SSH)", "cutting access to the server (network or SSH)"),
    (r"(?i)\bdrop\s+(database|table)\b", "удаление базы данных", "dropping a database"),
    (r"(?i)\bdocker\s+(system\s+prune|rm\s+-f|volume\s+rm)\b", "удаление контейнеров или томов Docker",
     "deleting Docker containers or volumes"),
    (r"(?i)\b(apt-get|apt|dnf|yum|pacman|zypper|apk|brew|winget|choco)\b[^|;&]*\b(remove|purge|uninstall|-R|-Rns)\b",
     "удаление пакетов", "removing packages"),
    (r"(?i)\bgit\s+(reset\s+--hard|clean\s+-\S*f)", "потерю несохранённых изменений в git",
     "losing uncommitted git changes"),
    (r"(?i)\b(crontab\s+-r)\b", "удаление всех задач cron", "deleting every cron job"),
)

_SELF_VERBS = re.compile(r"(?i)\b(rm|rmdir|mv|cp|dd|shred|truncate|unlink|del|erase|move|rename|chmod|chown|"
                         r"Remove-Item|Set-Content|Out-File|Clear-Content|kill|pkill|killall|taskkill)\b|>\s*\S")
_SERVICE_RE = re.compile(r"(?i)\b(systemctl|service|sc|launchctl)\b[^|;&]*\b(memuai[-\w]*)\b")
_KILL_SELF_RE = re.compile(r"(?i)\b(pkill|killall|taskkill)\b[^|;&]*\b(memuai\S*|dfy_agent|memuai_remote_agent)\b")


def _norm(path: Any) -> Optional[Path]:
    try:
        return Path(str(path)).expanduser().resolve()
    except (OSError, ValueError, RuntimeError):
        return None


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_memuai_dir(folder: Path) -> bool:
    """Папка установки или исходников MemuAI (по характерным файлам)."""
    try:
        if (folder / "MemuAI.exe").is_file() or (folder / "MemuAI Proxy.exe").is_file():
            return True
        app = folder / "app"
        return (app / "dfy_agent.py").is_file() and ((app / "playground_api.py").is_file() or (folder / "memuai.py").is_file())
    except OSError:
        return False


def memuai_tree(path: Path, depth: int = 3) -> Optional[Path]:
    """Папка MemuAI, которую задевает операция над ``path``: сам путь или его родитель —
    папка MemuAI, либо MemuAI лежит внутри ``path`` не глубже ``depth`` уровней.

    Так агент защищает MemuAI на СВОЕЙ машине, даже если запущен копией из другого
    каталога и MemuAI не прислал свои пути (а на чужом сервере MemuAI и вовсе нет)."""
    for folder in [path, *path.parents][:12]:
        if _is_memuai_dir(folder):
            return folder
    try:
        if not path.is_dir() or path.is_symlink():
            return None
    except OSError:
        return None
    level = [path]
    for _ in range(depth):
        nxt: List[Path] = []
        for folder in level:
            try:
                children = [c for c in folder.iterdir() if c.is_dir() and not c.is_symlink()]
            except OSError:
                continue
            for child in children[:400]:
                if _is_memuai_dir(child):
                    return child
                nxt.append(child)
        level = nxt[:4000]
        if not level:
            break
    return None


class Guard:
    """Правила безопасности агента: пути, которые нельзя трогать, и уровни команд."""

    def __init__(self, *, protect: Optional[List[str]] = None, roots: Optional[List[str]] = None,
                 state_dir: Optional[Path] = None) -> None:
        self.self_paths: List[Path] = []
        here = _norm(__file__)
        if here:
            self.self_paths.append(here)
        if state_dir:
            resolved = _norm(state_dir)
            if resolved:
                self.self_paths.append(resolved)
        for item in protect or []:
            resolved = _norm(item)
            if resolved:
                self.self_paths.append(resolved)
        self.system_paths = [p for p in (_norm(x) for x in (_SYSTEM_ROOTS_WIN if WINDOWS else _SYSTEM_ROOTS_POSIX)) if p]
        self.roots = [p for p in (_norm(x) for x in roots or []) if p]

    def narrowed(self, roots: List[str]) -> "Guard":
        """Копия с более узким белым списком (MemuAI может только сузить корни агента, не расширить)."""
        wanted = [p for p in (_norm(x) for x in roots) if p]
        if self.roots:
            wanted = [p for p in wanted if any(p == r or _inside(p, r) for r in self.roots)]
        if not wanted:
            raise Refused("Каталоги из настроек машины вне разрешённых агентом",
                          "The machine's directories are outside the ones the agent allows")
        clone = Guard.__new__(Guard)
        clone.self_paths, clone.system_paths, clone.roots = self.self_paths, self.system_paths, wanted
        return clone

    def widened(self, protect: List[str]) -> "Guard":
        """Копия с дополнительными защищёнными путями (их присылает MemuAI). Защиту можно только расширить."""
        extra = [p for p in (_norm(x) for x in protect[:32]) if p and p not in self.system_paths]
        if not extra:
            return self
        clone = Guard.__new__(Guard)
        clone.self_paths, clone.system_paths, clone.roots = self.self_paths + extra, self.system_paths, self.roots
        return clone

    # -- пути ---------------------------------------------------------------
    def owned(self, path: Path) -> bool:
        """Путь — агент, его состояние или MemuAI (сам путь, внутри или родитель)?"""
        for own in self.self_paths:
            if path == own or _inside(path, own) or _inside(own, path):
                return True
        return memuai_tree(path) is not None

    def protected_reason(self, path: Path) -> Optional[Tuple[str, str]]:
        """``(ru, en)``, если по этому пути нельзя писать/удалять; иначе ``None``."""
        if path in self.system_paths:
            return (f"«{path}» — системный каталог, его удаление сломает машину",
                    f"“{path}” is a system directory; deleting it would break the machine")
        if self.owned(path):
            return ("Это файлы самого агента MemuAI или MemuAI — агент никогда их не изменяет и не удаляет",
                    "These are the MemuAI agent's own files or MemuAI itself — the agent never changes or deletes them")
        for root in self.system_paths:
            if path == root or _inside(root, path):
                return (f"«{path}» — системный каталог, его удаление сломает машину",
                        f"“{path}” is a system directory; deleting it would break the machine")
        return None

    def check_write(self, path: Any, *, what: str = "изменить") -> Path:
        resolved = _norm(path)
        if resolved is None:
            raise Refused("Некорректный путь", "Invalid path")
        reason = self.protected_reason(resolved)
        if reason:
            raise Refused(f"Нельзя {what} «{resolved}»: {reason[0]}", f"Cannot touch “{resolved}”: {reason[1]}")
        self.check_root(resolved)
        return resolved

    def check_read(self, path: Any) -> Path:
        resolved = _norm(path)
        if resolved is None:
            raise Refused("Некорректный путь", "Invalid path")
        self.check_root(resolved)
        return resolved

    def check_root(self, path: Path) -> None:
        if not self.roots:
            return
        if not any(path == root or _inside(path, root) for root in self.roots):
            allowed = ", ".join(str(r) for r in self.roots)
            raise Refused(f"Путь вне разрешённых каталогов агента ({allowed})",
                          f"The path is outside the agent's allowed roots ({allowed})")

    # -- команды ------------------------------------------------------------
    def classify(self, command: str, cwd: Optional[str] = None) -> Dict[str, str]:
        """``{"level": safe|confirm|blocked, "reason", "reason_en"}`` для команды оболочки."""
        text = str(command or "")
        for pattern, ru, en in _FATAL_PATTERNS:
            if re.search(pattern, text):
                return {"level": "blocked", "reason": ru, "reason_en": en}
        if _SERVICE_RE.search(text) or _KILL_SELF_RE.search(text):
            return {"level": "blocked", "reason": "команда останавливает сам агент или MemuAI",
                    "reason_en": "the command would stop the agent or MemuAI itself"}
        if _SELF_VERBS.search(text):
            for resolved in self._path_args(text, cwd):
                if self.owned(resolved):
                    return {"level": "blocked",
                            "reason": "команда изменяет файлы самого агента или MemuAI",
                            "reason_en": "the command would change the agent's or MemuAI's own files"}
        for pattern, ru, en in _DANGER_PATTERNS:
            if re.search(pattern, text):
                return {"level": "confirm", "reason": ru, "reason_en": en}
        return {"level": "safe", "reason": "", "reason_en": ""}

    @staticmethod
    def _tokens(command: str) -> List[str]:
        try:
            parts = shlex.split(command, posix=not WINDOWS)
        except ValueError:
            parts = command.split()
        tokens: List[str] = []
        for part in parts:
            # «echo x>file», «a&&b», «2>>log» — операторы оболочки отделяем от путей
            for piece in re.split(r"[;&|<>]+", part):
                piece = re.sub(r"^\d+$", "", piece)
                if piece and not piece.startswith("-"):
                    tokens.append(piece)
        return tokens

    @classmethod
    def _path_args(cls, command: str, cwd: Optional[str]) -> List[Path]:
        """Аргументы команды, которые на деле являются путями.

        Относительное слово считаем путём, только если оно похоже на путь
        (``./x``, ``a/b``, ``~``, ``..``) или реально существует в рабочем
        каталоге: иначе «echo», «hello» или имя пакета при запуске из папки
        MemuAI превращались бы в «файлы MemuAI» и команда отклонялась бы.
        """
        base = cwd or os.getcwd()
        found: List[Path] = []
        for token in cls._tokens(command):
            pathish = os.path.isabs(token) or token.startswith(("~", ".")) or "/" in token or (WINDOWS and "\\" in token)
            candidate = token if os.path.isabs(token) or token.startswith("~") else os.path.join(base, token)
            if not pathish and not os.path.lexists(candidate):
                continue
            resolved = _norm(candidate)
            if resolved is not None:
                found.append(resolved)
        return found


# ----------------------------------------------------------------- ops ---
class Agent:
    """Операции над машиной. Один экземпляр и у агента, и у локального режима MemuAI."""

    def __init__(self, guard: Optional[Guard] = None, *, max_output: int = MAX_OUTPUT,
                 state: Optional[Path] = None) -> None:
        self.guard = guard or Guard()
        self._state = Path(state) if state else _state_dir()
        # общий для «суженных» копий агента (call() делает copy.copy с другим guard): присваивать
        # self._browser в копии нельзя — браузер потерялся бы для туннеля и browser_stop
        self._browser: Dict[str, Any] = {}
        self.max_output = int(max_output)
        self.jobs: Dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    # -- сведения -----------------------------------------------------------
    def ping(self, _params: Dict[str, Any]) -> Dict[str, Any]:
        return {"agent": AGENT_NAME, "version": AGENT_VERSION, "protocol": PROTOCOL,
                "os": platform.system().lower(), "hostname": socket.gethostname(), "time": time.time()}

    def sysinfo(self, _params: Dict[str, Any]) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "os": platform.system(), "os_release": platform.release(), "platform": platform.platform(),
            "arch": platform.machine(), "hostname": socket.gethostname(), "python": platform.python_version(),
            "user": self._user(), "cwd": os.getcwd(), "home": str(Path.home()), "shell": self._shell_name(),
            "agent_version": AGENT_VERSION, "cpu_count": os.cpu_count() or 0,
            "package_managers": self.package_managers(), "roots": [str(r) for r in self.guard.roots],
        }
        distro = self._distro()
        if distro:
            info["distro"] = distro
        try:
            usage = shutil.disk_usage(os.getcwd())
            info["disk"] = {"total_gb": round(usage.total / 2 ** 30, 1), "free_gb": round(usage.free / 2 ** 30, 1)}
        except OSError:
            pass
        memory = self._memory()
        if memory:
            info["memory"] = memory
        uptime = self._uptime()
        if uptime:
            info["uptime_hours"] = uptime
        return info

    @staticmethod
    def _user() -> str:
        for key in ("USER", "USERNAME", "LOGNAME"):
            if os.environ.get(key):
                return os.environ[key]
        try:
            import getpass
            return getpass.getuser()
        except Exception:
            return ""

    @staticmethod
    def _shell_name() -> str:
        if WINDOWS:
            return os.environ.get("COMSPEC") or "cmd.exe"
        return os.environ.get("SHELL") or "/bin/sh"

    @staticmethod
    def _distro() -> str:
        try:
            text = Path("/etc/os-release").read_text("utf-8", "replace")
        except OSError:
            return ""
        match = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', text, re.M)
        return match.group(1) if match else ""

    @staticmethod
    def _memory() -> Dict[str, float]:
        try:
            text = Path("/proc/meminfo").read_text("utf-8", "replace")
            total = int(re.search(r"MemTotal:\s+(\d+)", text).group(1))
            free = int(re.search(r"MemAvailable:\s+(\d+)", text).group(1))
            return {"total_gb": round(total / 2 ** 20, 1), "available_gb": round(free / 2 ** 20, 1)}
        except Exception:
            return {}

    @staticmethod
    def _uptime() -> float:
        try:
            return round(float(Path("/proc/uptime").read_text("ascii").split()[0]) / 3600, 1)
        except Exception:
            return 0.0

    @staticmethod
    def package_managers() -> List[str]:
        names = ("apt-get", "dnf", "yum", "pacman", "zypper", "apk", "brew", "snap", "pip3", "npm",
                 "winget", "choco") if not WINDOWS else ("winget", "choco", "pip", "npm")
        return [name for name in names if shutil.which(name)]

    # -- команды ------------------------------------------------------------
    def exec(self, params: Dict[str, Any], on_chunk: Optional[Callable[[str, str], None]] = None) -> Dict[str, Any]:
        command = str(params.get("command") or "").strip()
        if not command:
            raise Refused("Пустая команда", "Empty command")
        cwd = str(params.get("cwd") or "") or None
        if cwd:
            directory = self.guard.check_read(cwd)
            if not directory.is_dir():
                raise Refused(f"Каталог не найден: {directory}", f"No such directory: {directory}")
            cwd = str(directory)
        verdict = self.guard.classify(command, cwd)
        if verdict["level"] == "blocked":
            raise Refused(f"Команда запрещена: {verdict['reason']}", f"Command refused: {verdict['reason_en']}")
        if verdict["level"] == "confirm" and not params.get("confirmed"):
            raise NeedsConfirm(f"Команда выполняет {verdict['reason']} — нужно подтверждение пользователя",
                               f"The command performs {verdict['reason_en']} — the user must confirm it")
        try:
            timeout = max(1.0, min(MAX_TIMEOUT, float(params.get("timeout") or DEFAULT_TIMEOUT)))
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
        env = dict(os.environ)
        extra = params.get("env")
        if isinstance(extra, dict):
            env.update({str(k): str(v) for k, v in list(extra.items())[:40]})
        env.setdefault("DEBIAN_FRONTEND", "noninteractive")
        job = str(params.get("job") or secrets.token_hex(4))
        started = time.time()
        popen_kwargs: Dict[str, Any] = dict(cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if not WINDOWS:
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(command, shell=True, **popen_kwargs)
        with self._lock:
            self.jobs[job] = process
        chunks: List[str] = []
        size = 0
        truncated = False
        timed_out = threading.Event()

        def expire() -> None:
            timed_out.set()
            self._terminate(process)
        watchdog = threading.Timer(timeout, expire)
        watchdog.daemon = True
        watchdog.start()
        try:
            assert process.stdout is not None
            for raw in iter(process.stdout.readline, b""):
                text = raw.decode("utf-8", "replace")
                if size < self.max_output:
                    piece = text[:self.max_output - size]
                    chunks.append(piece)
                    size += len(piece)
                    if on_chunk:
                        on_chunk(job, piece)
                else:
                    truncated = True
            process.wait(timeout=10)
        except BaseException:
            self._terminate(process)
            raise
        finally:
            watchdog.cancel()
            with self._lock:
                self.jobs.pop(job, None)
            try:
                if process.stdout:
                    process.stdout.close()
            except Exception:
                pass
        status = "timeout" if timed_out.is_set() else "cancelled" if getattr(process, "_memu_cancelled", False) else "ok"
        return {"job": job, "command": command, "cwd": cwd or os.getcwd(), "exit_code": process.returncode,
                "status": status, "output": "".join(chunks), "truncated": truncated,
                "duration": round(time.time() - started, 2), "danger": verdict["level"]}

    def exec_cancel(self, params: Dict[str, Any]) -> Dict[str, Any]:
        job = str(params.get("job") or "")
        with self._lock:
            process = self.jobs.get(job)
        if process is None:
            return {"ok": False, "error": "Задание уже завершено", "error_en": "The job already finished"}
        setattr(process, "_memu_cancelled", True)
        self._terminate(process)
        return {"ok": True, "job": job}

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        try:
            if WINDOWS:
                process.terminate()
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            try:
                process.wait(timeout=5)
                return
            except subprocess.TimeoutExpired:
                pass
            if WINDOWS:
                process.kill()
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    # -- файлы --------------------------------------------------------------
    def fs_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        path = self.guard.check_read(params.get("path") or ".")
        if not path.is_dir():
            raise Refused(f"Не каталог: {path}", f"Not a directory: {path}")
        rows: List[Dict[str, Any]] = []
        try:
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as exc:
            raise Refused(f"Не удалось прочитать каталог: {exc}", f"Could not read the directory: {exc}") from None
        limit = max(1, min(MAX_LIST, int(params.get("limit") or MAX_LIST)))
        for item in entries[:limit]:
            row: Dict[str, Any] = {"name": item.name, "type": "dir" if item.is_dir() else "file"}
            if item.is_symlink():
                row["symlink"] = True
            try:
                stat = item.stat()
                row["size"] = stat.st_size
                row["modified"] = int(stat.st_mtime)
                if not WINDOWS:
                    row["mode"] = oct(stat.st_mode & 0o777)[2:]
            except OSError:
                row["unreadable"] = True
            rows.append(row)
        return {"path": str(path), "entries": rows, "total": len(entries), "shown": len(rows)}

    def fs_stat(self, params: Dict[str, Any]) -> Dict[str, Any]:
        path = self.guard.check_read(params.get("path") or ".")
        if not path.exists():
            return {"path": str(path), "exists": False}
        stat = path.stat()
        out = {"path": str(path), "exists": True, "type": "dir" if path.is_dir() else "file",
               "size": stat.st_size, "modified": int(stat.st_mtime)}
        if not WINDOWS:
            out["mode"] = oct(stat.st_mode & 0o777)[2:]
        protection = self.guard.protected_reason(path)
        if protection:
            out["protected"] = protection[0]
            out["protected_en"] = protection[1]
        return out

    def fs_read(self, params: Dict[str, Any]) -> Dict[str, Any]:
        path = self.guard.check_read(params.get("path") or "")
        if not path.is_file():
            raise Refused(f"Файл не найден: {path}", f"No such file: {path}")
        limit = max(1, min(MAX_READ, int(params.get("max_bytes") or MAX_READ)))
        offset = max(0, int(params.get("offset") or 0))
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read(limit)
        if params.get("binary"):
            return {"path": str(path), "size": size, "offset": offset,
                    "content_b64": base64.b64encode(data).decode("ascii"),
                    "truncated": offset + len(data) < size}
        return {"path": str(path), "size": size, "offset": offset, "text": data.decode("utf-8", "replace"),
                "truncated": offset + len(data) < size}

    def fs_write(self, params: Dict[str, Any]) -> Dict[str, Any]:
        path = self.guard.check_write(params.get("path") or "", what="записать в")
        append = bool(params.get("append"))
        if params.get("content_b64") is not None:
            data = base64.b64decode(str(params.get("content_b64") or ""))
        else:
            data = str(params.get("text") or "").encode("utf-8")
        existed = path.exists()
        if existed and path.is_dir():
            raise Refused(f"Это каталог: {path}", f"This is a directory: {path}")
        if existed and not append and not params.get("confirmed") and path.stat().st_size > 0:
            raise NeedsConfirm(f"Файл «{path}» уже существует и будет перезаписан",
                               f"The file “{path}” already exists and would be overwritten")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab" if append else "wb") as handle:
            handle.write(data)
        return {"path": str(path), "bytes": len(data), "appended": append, "created": not existed,
                "size": path.stat().st_size}

    def fs_mkdir(self, params: Dict[str, Any]) -> Dict[str, Any]:
        path = self.guard.check_write(params.get("path") or "", what="создать")
        path.mkdir(parents=True, exist_ok=True)
        return {"path": str(path), "ok": True}

    def fs_delete(self, params: Dict[str, Any]) -> Dict[str, Any]:
        path = self.guard.check_write(params.get("path") or "", what="удалить")
        if not path.exists() and not path.is_symlink():
            raise Refused(f"Нечего удалять: {path}", f"Nothing to delete: {path}")
        recursive = bool(params.get("recursive"))
        is_dir = path.is_dir() and not path.is_symlink()
        if not params.get("confirmed"):
            raise NeedsConfirm(f"Удаление «{path}»" + (" со всем содержимым" if is_dir and recursive else ""),
                               f"Deleting “{path}”" + (" with everything inside" if is_dir and recursive else ""))
        if is_dir:
            if not recursive:
                try:
                    path.rmdir()
                except OSError as exc:
                    raise Refused(f"Каталог не пуст: {exc}", f"The directory is not empty: {exc}") from None
            else:
                shutil.rmtree(path)
        else:
            path.unlink()
        return {"path": str(path), "deleted": True, "was": "dir" if is_dir else "file"}

    def fs_move(self, params: Dict[str, Any]) -> Dict[str, Any]:
        source = self.guard.check_write(params.get("source") or params.get("src") or "", what="переместить")
        target = self.guard.check_write(params.get("target") or params.get("dst") or "", what="записать в")
        if not source.exists():
            raise Refused(f"Нечего перемещать: {source}", f"Nothing to move: {source}")
        if target.exists() and not params.get("confirmed"):
            raise NeedsConfirm(f"«{target}» уже существует и будет заменён",
                               f"“{target}” already exists and would be replaced")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        return {"source": str(source), "target": str(target), "moved": True}

    def fs_find(self, params: Dict[str, Any]) -> Dict[str, Any]:
        path = self.guard.check_read(params.get("path") or ".")
        pattern = str(params.get("pattern") or "*")[:200]
        limit = max(1, min(200, int(params.get("limit") or 60)))
        rows: List[Dict[str, Any]] = []
        try:
            for item in path.rglob(pattern):
                rows.append({"path": str(item), "type": "dir" if item.is_dir() else "file"})
                if len(rows) >= limit:
                    break
        except OSError as exc:
            raise Refused(f"Поиск не удался: {exc}", f"The search failed: {exc}") from None
        return {"path": str(path), "pattern": pattern, "matches": rows}

    def fetch(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Скачать файл по URL прямо на машину (http/https)."""
        import urllib.request
        url = str(params.get("url") or "").strip()
        if not re.match(r"(?i)^https?://", url):
            raise Refused("Скачивание только по http(s)", "Downloads are http(s) only")
        path = self.guard.check_write(params.get("path") or "", what="записать в")
        if path.is_dir():
            name = os.path.basename(url.split("?")[0]) or "download"
            path = self.guard.check_write(path / name, what="записать в")
        if path.exists() and not params.get("confirmed"):
            raise NeedsConfirm(f"Файл «{path}» будет перезаписан", f"The file “{path}” would be overwritten")
        limit = max(1, min(2 * 1024 ** 3, int(params.get("max_bytes") or 512 * 1024 ** 2)))
        path.parent.mkdir(parents=True, exist_ok=True)
        proxy = str(params.get("proxy") or "")
        if proxy.lower().startswith("socks"):
            return self._fetch_curl(url, path, limit, proxy)
        # без прокси — напрямую, даже если в окружении агента заданы *_PROXY (решает MemuAI)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(
            {"http": proxy, "https": proxy} if proxy else {}))
        opener.addheaders = [("User-Agent", f"{AGENT_NAME}/{AGENT_VERSION}")]
        total = 0
        with opener.open(url, timeout=60) as response, path.open("wb") as handle:
            while True:
                block = response.read(262_144)
                if not block:
                    break
                total += len(block)
                if total > limit:
                    handle.close()
                    path.unlink(missing_ok=True)
                    raise Refused(f"Файл больше лимита ({limit} байт)", f"The file is over the limit ({limit} bytes)")
                handle.write(block)
        return {"url": url, "path": str(path), "bytes": total}

    def _fetch_curl(self, url: str, path: Path, limit: int, proxy: str) -> Dict[str, Any]:
        """SOCKS-прокси urllib не умеет — скачиваем через curl (имена разрешает прокси: socks5h)."""
        curl = shutil.which("curl")
        if not curl:
            raise Refused("Для скачивания через SOCKS-прокси на машине нужен curl",
                          "Downloading through a SOCKS proxy needs curl on the machine")
        proxy = re.sub(r"(?i)^socks5://", "socks5h://", proxy)
        # прокси с паролем — через окружение, а не в argv (argv виден всем в ps)
        env = {**os.environ, "ALL_PROXY": proxy, "all_proxy": proxy}
        done = subprocess.run([curl, "-fsSL", "--max-filesize", str(limit), "--max-time", "1800",
                               "-A", f"{AGENT_NAME}/{AGENT_VERSION}", "-o", str(path), url], env=env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1900)
        if done.returncode != 0:
            path.unlink(missing_ok=True)
            text = done.stdout.decode("utf-8", "replace").strip()[-300:]
            raise Refused(f"Скачивание через прокси не удалось: {text}", f"The download through the proxy failed: {text}")
        return {"url": url, "path": str(path), "bytes": path.stat().st_size}

    # -- пакеты и процессы --------------------------------------------------
    PKG_COMMANDS = {
        "apt-get": {"install": "apt-get install -y {name}", "remove": "apt-get remove -y {name}",
                    "list": "apt list --installed", "search": "apt-cache search {name}", "update": "apt-get update"},
        "dnf": {"install": "dnf install -y {name}", "remove": "dnf remove -y {name}",
                "list": "dnf list installed", "search": "dnf search {name}", "update": "dnf check-update"},
        "yum": {"install": "yum install -y {name}", "remove": "yum remove -y {name}",
                "list": "yum list installed", "search": "yum search {name}", "update": "yum check-update"},
        "pacman": {"install": "pacman -S --noconfirm {name}", "remove": "pacman -Rns --noconfirm {name}",
                   "list": "pacman -Q", "search": "pacman -Ss {name}", "update": "pacman -Sy"},
        "apk": {"install": "apk add {name}", "remove": "apk del {name}", "list": "apk info",
                "search": "apk search {name}", "update": "apk update"},
        "zypper": {"install": "zypper --non-interactive install {name}", "remove": "zypper --non-interactive remove {name}",
                   "list": "zypper se -i", "search": "zypper se {name}", "update": "zypper refresh"},
        "brew": {"install": "brew install {name}", "remove": "brew uninstall {name}", "list": "brew list",
                 "search": "brew search {name}", "update": "brew update"},
        "pip3": {"install": "pip3 install {name}", "remove": "pip3 uninstall -y {name}", "list": "pip3 list",
                 "search": "pip3 index versions {name}", "update": "pip3 install --upgrade pip"},
        "npm": {"install": "npm install -g {name}", "remove": "npm uninstall -g {name}", "list": "npm ls -g --depth=0",
                "search": "npm search {name}", "update": "npm update -g"},
        "winget": {"install": "winget install --silent --accept-package-agreements {name}",
                   "remove": "winget uninstall --silent {name}", "list": "winget list",
                   "search": "winget search {name}", "update": "winget upgrade"},
        "choco": {"install": "choco install -y {name}", "remove": "choco uninstall -y {name}",
                  "list": "choco list --local-only", "search": "choco search {name}", "update": "choco upgrade all -y"},
    }

    def pkg(self, params: Dict[str, Any], on_chunk: Optional[Callable[[str, str], None]] = None) -> Dict[str, Any]:
        action = str(params.get("action") or "install").lower()
        if action not in ("install", "remove", "list", "search", "update"):
            raise Refused(f"Неизвестное действие с пакетами: {action}", f"Unknown package action: {action}")
        manager = str(params.get("manager") or "").strip() or self._pick_manager()
        if manager not in self.PKG_COMMANDS:
            raise Refused(f"Менеджер пакетов «{manager}» не поддерживается или не найден",
                          f"Package manager “{manager}” is not supported or not installed")
        if not shutil.which(manager):
            raise Refused(f"На машине нет «{manager}»", f"“{manager}” is not installed on the machine")
        name = str(params.get("name") or "").strip()
        if action in ("install", "remove", "search") and not re.fullmatch(r"[A-Za-z0-9 ._+@:/-]{1,120}", name):
            raise Refused("Некорректное имя пакета", "Invalid package name")
        template = self.PKG_COMMANDS[manager][action]
        command = template.format(name=" ".join(shlex.quote(part) for part in name.split()) if name else "")
        if action in ("install", "remove", "update") and not WINDOWS and os.geteuid() != 0 and shutil.which("sudo") \
                and manager not in ("pip3", "npm", "brew"):
            command = "sudo -n " + command
        if action == "remove" and not params.get("confirmed"):
            raise NeedsConfirm(f"Удаление пакета «{name}» ({manager})",
                               f"Removing the package “{name}” ({manager})")
        result = self.exec({"command": command, "cwd": params.get("cwd"), "timeout": params.get("timeout") or 900,
                            "env": params.get("env"), "confirmed": True}, on_chunk)
        result["manager"] = manager
        result["package"] = name
        result["pkg_action"] = action
        return result

    def _pick_manager(self) -> str:
        for name in ("apt-get", "dnf", "yum", "pacman", "zypper", "apk", "winget", "choco", "brew"):
            if shutil.which(name):
                return name
        return ""

    def proc_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = str(params.get("query") or "").strip().lower()
        limit = max(1, min(200, int(params.get("limit") or 40)))
        rows: List[Dict[str, Any]] = []
        if WINDOWS:
            raw = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=30)
            for line in raw.stdout.splitlines():
                parts = [p.strip('"') for p in line.split('","')]
                if len(parts) < 5:
                    continue
                rows.append({"pid": int(re.sub(r"\D", "", parts[1]) or 0), "name": parts[0], "memory": parts[4]})
        else:
            raw = subprocess.run(["ps", "-eo", "pid,pcpu,pmem,comm,args", "--sort=-pcpu"],
                                 capture_output=True, text=True, timeout=30)
            for line in raw.stdout.splitlines()[1:]:
                parts = line.split(None, 4)
                if len(parts) < 5:
                    continue
                rows.append({"pid": int(parts[0]) if parts[0].isdigit() else 0, "cpu": parts[1], "memory": parts[2],
                             "name": parts[3], "command": parts[4][:200]})
        if query:
            rows = [row for row in rows if query in json.dumps(row, ensure_ascii=False).lower()]
        return {"processes": rows[:limit], "total": len(rows)}

    def proc_kill(self, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            pid = int(params.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid <= 0:
            raise Refused("Нужен корректный PID", "A valid PID is required")
        if pid in (os.getpid(), os.getppid()):
            raise Refused("Нельзя завершить сам агент", "The agent cannot kill itself")
        if self._is_own_process(pid):
            raise Refused("Это процесс агента MemuAI или MemuAI — он защищён",
                          "This is the MemuAI agent's or MemuAI's own process — it is protected")
        if not params.get("confirmed"):
            raise NeedsConfirm(f"Завершение процесса {pid}", f"Killing process {pid}")
        force = bool(params.get("force"))
        try:
            if WINDOWS:
                subprocess.run(["taskkill", "/PID", str(pid)] + (["/F"] if force else []), capture_output=True,
                               timeout=20)
            else:
                os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
        except (OSError, subprocess.SubprocessError) as exc:
            raise Refused(f"Не удалось завершить процесс: {exc}", f"Could not kill the process: {exc}") from None
        return {"pid": pid, "killed": True, "force": force}

    def _is_own_process(self, pid: int) -> bool:
        if WINDOWS:
            return False
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace").replace("\0", " ")
        except OSError:
            return False
        lowered = cmdline.lower()
        if "memuai_remote_agent" in lowered or "dfy_agent" in lowered:
            return True
        for own in self.guard.self_paths:
            if str(own).lower() in lowered:
                return True
        return False

    # -- браузер на этой машине (для DFY: CDP через туннель агента) ----------
    BROWSERS = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "microsoft-edge",
                "microsoft-edge-stable", "msedge", "chrome")

    def _browser_exe(self, explicit: str = "") -> str:
        candidates = [explicit, os.environ.get("MEMUAI_BROWSER_EXECUTABLE", "")]
        candidates += [shutil.which(name) or "" for name in self.BROWSERS]
        if WINDOWS:
            for base in (os.environ.get("PROGRAMFILES(X86)", ""), os.environ.get("PROGRAMFILES", ""),
                         os.environ.get("LOCALAPPDATA", "")):
                if base:
                    candidates += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                                   os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
        elif sys.platform == "darwin":
            candidates += ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                           "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                           "/Applications/Chromium.app/Contents/MacOS/Chromium"]
        return next((c for c in candidates if c and os.path.isabs(c) and os.path.isfile(c)), "")

    def browser_start(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Запустить Chromium/Edge с CDP на 127.0.0.1 этой машины (профиль — в каталоге агента)."""
        with self._lock:
            current = self._browser.get("current")
        if current and current["process"].poll() is None:
            return {"port": current["port"], "executable": current["executable"], "reused": True}
        exe = self._browser_exe(str(params.get("executable") or ""))
        if not exe:
            raise Refused("На машине нет Chromium/Chrome/Edge. Установите браузер (например «pkg install chromium»)",
                          "No Chromium/Chrome/Edge on the machine. Install one (e.g. pkg install chromium)")
        profile = self._state / "browser-profile"
        profile.mkdir(parents=True, exist_ok=True)
        port_file = profile / "DevToolsActivePort"
        port_file.unlink(missing_ok=True)
        args = [exe, "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", "--disable-sync",
                "--window-size=1280,860"]
        if not params.get("visible") or (not WINDOWS and sys.platform != "darwin" and not os.environ.get("DISPLAY")):
            args.append("--headless=new")
        if not WINDOWS and hasattr(os, "geteuid") and os.geteuid() == 0:
            args.append("--no-sandbox")
        args += [str(a) for a in params.get("args") or [] if str(a).startswith("--")][:20]
        # флаги владельца машины (контейнер без user namespaces: --no-sandbox и т.п.), как у браузера DFY
        try:
            own = shlex.split(os.environ.get("MEMUAI_BROWSER_ARGS", ""), posix=not WINDOWS)
        except ValueError:
            own = []
        args += [a for a in own if a.startswith("--")][:20]
        args.append("about:blank")
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=not WINDOWS)
        port = 0
        for _ in range(150):
            if process.poll() is not None:
                break
            try:
                port = int(port_file.read_text("ascii").splitlines()[0])
                break
            except (OSError, ValueError, IndexError):
                time.sleep(0.1)
        if not port:
            self._terminate(process)
            raise Refused("Браузер на машине не запустился", "The browser did not start on the machine")
        with self._lock:
            self._browser["current"] = {"process": process, "port": port, "executable": exe}
        return {"port": port, "executable": exe, "reused": False}

    def browser_stop(self, _params: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            current = self._browser.pop("current", None)
        if current:
            self._terminate(current["process"])
        return {"stopped": bool(current)}

    def browser_port(self) -> int:
        current = self._browser.get("current")
        return current["port"] if current and current["process"].poll() is None else 0

    # -- диспетчер ----------------------------------------------------------
    def call(self, op: str, params: Dict[str, Any],
             on_chunk: Optional[Callable[[str, str], None]] = None) -> Dict[str, Any]:
        handlers: Dict[str, Callable[..., Dict[str, Any]]] = {
            "ping": self.ping, "sysinfo": self.sysinfo, "exec": self.exec, "exec_cancel": self.exec_cancel,
            "fs_list": self.fs_list, "fs_stat": self.fs_stat, "fs_read": self.fs_read, "fs_write": self.fs_write,
            "fs_mkdir": self.fs_mkdir, "fs_delete": self.fs_delete, "fs_move": self.fs_move,
            "fs_find": self.fs_find, "fetch": self.fetch, "pkg": self.pkg, "proc_list": self.proc_list,
            "proc_kill": self.proc_kill, "browser_start": self.browser_start, "browser_stop": self.browser_stop,
        }
        handler = handlers.get(str(op or ""))
        if handler is None:
            raise Refused(f"Неизвестная операция: {op}", f"Unknown operation: {op}")
        params = dict(params or {})
        roots = params.pop("roots", None)
        protect = params.pop("protect", None)
        if isinstance(protect, list) and protect:
            import copy
            scoped = copy.copy(self)
            scoped.guard = self.guard.widened([str(p) for p in protect])
            return scoped.call(op, {**params, "roots": roots} if roots else params, on_chunk)
        if isinstance(roots, list) and roots:
            import copy
            scoped = copy.copy(self)
            scoped.guard = self.guard.narrowed([str(r) for r in roots])
            if op == "exec" and not params.get("cwd"):
                params["cwd"] = str(scoped.guard.roots[0])
            return scoped.call(op, params, on_chunk)
        if handler in (self.exec, self.pkg):
            return handler(params or {}, on_chunk)
        return handler(params or {})


# ------------------------------------------------------------- подпись ---
def sign(token: str, body: bytes, timestamp: Optional[int] = None, nonce: str = "") -> str:
    """``ts.nonce.hmac`` — заголовок ``X-Memu-Agent-Auth``. Токен по сети не уходит."""
    ts = int(timestamp if timestamp is not None else time.time())
    nonce = nonce or secrets.token_hex(8)
    digest = hashlib.sha256(body or b"").hexdigest()
    mac = hmac.new(token.encode("utf-8"), f"{ts}.{nonce}.{digest}".encode("ascii"), hashlib.sha256).hexdigest()
    return f"{ts}.{nonce}.{mac}"


def verify(token: str, body: bytes, header: str, seen: Optional[set] = None) -> bool:
    parts = str(header or "").split(".")
    if len(parts) != 3:
        return False
    ts_raw, nonce, mac = parts
    try:
        ts = int(ts_raw)
    except ValueError:
        return False
    if abs(time.time() - ts) > AUTH_WINDOW:
        return False
    if seen is not None:
        if nonce in seen:
            return False
        seen.add(nonce)
        if len(seen) > 4096:
            seen.clear()
    return hmac.compare_digest(sign(token, body, ts, nonce), header)


def pairing_code(host: str, port: int, token: str, *, name: str = "", tls: bool = False) -> str:
    payload = {"v": PROTOCOL, "h": host, "p": int(port), "t": token, "n": name or socket.gethostname(),
               "o": platform.system().lower(), "s": bool(tls)}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return "MEMU1-" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def parse_pairing_code(code: str) -> Dict[str, Any]:
    text = str(code or "").strip().replace(" ", "").replace("\n", "")
    if not text.upper().startswith("MEMU1-"):
        raise ValueError("Код подключения должен начинаться с MEMU1-")
    raw = text[6:]
    try:
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except Exception as exc:
        raise ValueError("Код подключения повреждён") from exc
    if not isinstance(data, dict) or not data.get("t"):
        raise ValueError("Код подключения повреждён")
    return {"host": str(data.get("h") or ""), "port": int(data.get("p") or DEFAULT_PORT),
            "token": str(data.get("t")), "name": str(data.get("n") or ""), "os": str(data.get("o") or ""),
            "tls": bool(data.get("s"))}


# --------------------------------------------------------------- сервер ---
def _state_dir() -> Path:
    base = os.environ.get("MEMUAI_AGENT_HOME") or os.path.join(str(Path.home()), ".memuai-agent")
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_token(explicit: str = "") -> str:
    if explicit and explicit != "auto":
        return explicit
    if os.environ.get("MEMUAI_AGENT_TOKEN"):
        return os.environ["MEMUAI_AGENT_TOKEN"]
    path = _state_dir() / "token"
    if path.is_file():
        value = path.read_text("ascii").strip()
        if value:
            return value
    token = secrets.token_urlsafe(32)
    path.write_text(token, "ascii")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def _primary_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def serve(args: Any) -> int:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    token = _load_token(args.token)
    agent = Agent(Guard(protect=args.protect, roots=args.root, state_dir=_state_dir()))
    seen_nonces: set = set()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = f"MemuAIAgent/{AGENT_VERSION}"

        def log_message(self, fmt: str, *fmt_args: Any) -> None:   # тише, чем http.server по умолчанию
            if args.verbose:
                sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % fmt_args))

        def _send(self, code: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:   # noqa: N802 (API http.server)
            if self.path.rstrip("/") in ("/hello", ""):
                self._send(200, {"agent": AGENT_NAME, "version": AGENT_VERSION, "protocol": PROTOCOL,
                                 "os": platform.system().lower(), "hostname": socket.gethostname(),
                                 "fingerprint": hashlib.sha256(token.encode()).hexdigest()[:12]})
                return
            self._send(404, {"error": "Нет такого маршрута", "error_en": "No such route"})

        def do_POST(self) -> None:   # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            if length > 64 * 1024 * 1024:
                self._send(413, {"error": "Слишком большой запрос", "error_en": "Request too large"})
                return
            body = self.rfile.read(length) if length else b""
            if not verify(token, body, self.headers.get("X-Memu-Agent-Auth") or "", seen_nonces):
                self._send(401, {"error": "Подпись не подошла", "error_en": "Bad signature"})
                return
            try:
                request = json.loads(body or b"{}")
            except ValueError:
                self._send(400, {"error": "Некорректный JSON", "error_en": "Invalid JSON"})
                return
            if self.path.rstrip("/") == "/tunnel":
                self._tunnel()
                return
            op = str(request.get("op") or "")
            params = request.get("params") if isinstance(request.get("params"), dict) else {}
            stream = self.path.rstrip("/").endswith("stream")
            if not stream:
                try:
                    self._send(200, {"ok": True, "result": agent.call(op, params)})
                except NeedsConfirm as exc:
                    self._send(409, {"ok": False, "needs_confirm": True, "error": str(exc),
                                     "error_en": exc.message_en})
                except Refused as exc:
                    self._send(403, {"ok": False, "error": str(exc), "error_en": exc.message_en})
                except Exception as exc:   # ошибка операции, не падение агента
                    self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self._stream(op, params)

        def _tunnel(self) -> None:
            """Сырой TCP-туннель к CDP браузера этой машины (только 127.0.0.1:порт браузера агента)."""
            port = agent.browser_port()
            if not port:
                self._send(409, {"error": "Браузер на машине не запущен", "error_en": "The browser is not running"})
                return
            try:
                upstream = socket.create_connection(("127.0.0.1", port), timeout=10)
            except OSError as exc:
                self._send(502, {"error": f"Нет связи с браузером: {exc}", "error_en": f"No browser: {exc}"})
                return
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.send_header("X-Memu-Tunnel", "1")
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            pipe(self.connection, upstream)

        def _write_chunk(self, payload: Dict[str, Any]) -> None:
            data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            self.wfile.write(b"%X\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()

        def _stream(self, op: str, params: Dict[str, Any]) -> None:
            def on_chunk(job: str, text: str) -> None:
                try:
                    self._write_chunk({"chunk": text, "job": job})
                except OSError:
                    pass
            try:
                result = agent.call(op, params, on_chunk)
                self._write_chunk({"ok": True, "result": result})
            except NeedsConfirm as exc:
                self._write_chunk({"ok": False, "needs_confirm": True, "error": str(exc), "error_en": exc.message_en})
            except Refused as exc:
                self._write_chunk({"ok": False, "error": str(exc), "error_en": exc.message_en})
            except Exception as exc:
                self._write_chunk({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except OSError:
                pass

    httpd = ThreadingHTTPServer((args.bind, int(args.port)), Handler)
    if args.tls_cert and args.tls_key:
        import ssl
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.tls_cert, args.tls_key)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    host = args.advertise or _primary_ip()
    port = httpd.server_address[1]
    print(f"{AGENT_NAME} {AGENT_VERSION} слушает {args.bind}:{port}"
          + (" (TLS)" if args.tls_cert else "") + f", хост для MemuAI: {host}")
    if args.root:
        print("Разрешённые каталоги: " + ", ".join(args.root))
    print("\nКод подключения (вставьте в MemuAI → Настройки → Do For You → Удалённые машины):\n")
    print("  " + pairing_code(host, port, token, tls=bool(args.tls_cert)))
    print("\nКод содержит токен — передавайте его только себе. Ctrl+C останавливает агента.", flush=True)

    def stop(*_signal: Any) -> None:     # systemctl stop / kill: как Ctrl+C — с уборкой браузера машины
        raise KeyboardInterrupt
    if hasattr(signal, "SIGTERM") and threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, stop)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        agent.browser_stop({})   # браузер запущен в своей сессии процессов и сам не закроется
    return 0


def pipe(left: socket.socket, right: socket.socket) -> None:
    """Гнать байты в обе стороны, пока одна из сторон не закроется."""
    left.settimeout(None)
    right.settimeout(None)

    def forward(source: socket.socket, target: socket.socket) -> None:
        try:
            while True:
                data = source.recv(65536)
                if not data:
                    break
                target.sendall(data)
        except OSError:
            pass
        for sock in (source, target):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    other = threading.Thread(target=forward, args=(right, left), daemon=True)
    other.start()
    forward(left, right)
    other.join(timeout=5)
    right.close()


def run_once(args: Any) -> int:
    """Режим SSH: одна операция. Запрос — JSON-строка в stdin, ответ — NDJSON в stdout.

    Пока операция идёт, stdin читается дальше: его закрытие (MemuAI отменил задание и
    закрыл SSH) останавливает команду."""
    line = sys.stdin.readline()
    try:
        request = json.loads(line or "{}")
    except ValueError:
        request = {}
    agent = Agent(Guard(protect=args.protect, roots=args.root, state_dir=_state_dir()))
    out = sys.stdout

    def emit(payload: Dict[str, Any]) -> None:
        out.write(json.dumps(payload, ensure_ascii=False) + "\n")
        out.flush()

    def watch_stdin() -> None:
        try:
            while sys.stdin.read(1):
                pass
        except Exception:
            pass
        for job in list(agent.jobs):
            agent.exec_cancel({"job": job})
    threading.Thread(target=watch_stdin, daemon=True).start()
    op = str(request.get("op") or "")
    params = request.get("params") if isinstance(request.get("params"), dict) else {}
    try:
        emit({"ok": True, "result": agent.call(op, params, lambda job, text: emit({"chunk": text, "job": job}))})
    except NeedsConfirm as exc:
        emit({"ok": False, "needs_confirm": True, "error": str(exc), "error_en": exc.message_en})
    except Refused as exc:
        emit({"ok": False, "error": str(exc), "error_en": exc.message_en})
    except Exception as exc:
        emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    if op == "browser_start" and agent.browser_port():
        # браузер живёт, пока открыт SSH-туннель: ждём закрытия stdin
        try:
            while sys.stdin.read(1):
                pass
        except Exception:
            pass
        agent.browser_stop({})
    return 0


SYSTEMD_UNIT = """[Unit]
Description={name}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
Environment=MEMUAI_AGENT_HOME={home}
ExecStart={python} {script} serve --bind {bind} --port {port}{roots}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def systemd(args: Any) -> int:
    script = str(Path(__file__).resolve())
    roots = "".join(f" --root {shlex.quote(r)}" for r in (args.root or []))
    unit = SYSTEMD_UNIT.format(name=AGENT_NAME, user=Agent._user() or "root", home=str(_state_dir()),
                               python=sys.executable, script=script, bind=args.bind, port=args.port, roots=roots)
    print(unit)
    print("# Сохраните как /etc/systemd/system/memuai-agent.service, затем:\n"
          "#   sudo systemctl daemon-reload && sudo systemctl enable --now memuai-agent\n"
          "#   journalctl -u memuai-agent -f     # код подключения печатается при старте\n"
          f"#   {sys.executable} {script} code   # показать код подключения ещё раз")
    return 0


def show_code(args: Any) -> int:
    token = _load_token(args.token)
    host = args.advertise or _primary_ip()
    print(pairing_code(host, int(args.port), token, tls=False))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="memuai_remote_agent", description=f"{AGENT_NAME} {AGENT_VERSION}")
    sub = parser.add_subparsers(dest="command")

    def common(target: Any) -> None:
        target.add_argument("--root", action="append", default=[],
                            help="разрешить файловые операции только в этом каталоге (можно несколько)")
        target.add_argument("--protect", action="append", default=[],
                            help="дополнительный путь, который агент никогда не трогает")

    run = sub.add_parser("serve", help="принимать подключения MemuAI (по умолчанию)")
    run.add_argument("--bind", default="0.0.0.0")
    run.add_argument("--port", type=int, default=DEFAULT_PORT)
    run.add_argument("--token", default="auto")
    run.add_argument("--advertise", default="", help="адрес, который увидит MemuAI (IP или домен)")
    run.add_argument("--tls-cert", default="")
    run.add_argument("--tls-key", default="")
    run.add_argument("--verbose", action="store_true")
    common(run)

    unit = sub.add_parser("systemd", help="напечатать юнит systemd")
    unit.add_argument("--bind", default="0.0.0.0")
    unit.add_argument("--port", type=int, default=DEFAULT_PORT)
    common(unit)

    once = sub.add_parser("once", help="одна операция из stdin (так MemuAI работает по SSH)")
    common(once)

    code = sub.add_parser("code", help="показать код подключения")
    code.add_argument("--port", type=int, default=DEFAULT_PORT)
    code.add_argument("--token", default="auto")
    code.add_argument("--advertise", default="")

    args = parser.parse_args(argv)
    if args.command in (None, "serve"):
        if args.command is None:
            args = parser.parse_args(["serve"] + list(argv or []))
        return serve(args)
    if args.command == "systemd":
        return systemd(args)
    if args.command == "once":
        return run_once(args)
    return show_code(args)


if __name__ == "__main__":
    sys.exit(main())
