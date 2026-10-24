"""MemuCode sandbox: Windows Job Object process isolation.

Every session gets one job. Processes launched by the agent are assigned to
it, the job has memory/CPU/time limits, and Stop or session close kills the
whole tree. No Docker and no project copies are required.
"""
from __future__ import annotations

import codecs
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_MEMORY_LIMIT = 2 * 1024 * 1024 * 1024
DEFAULT_CPU_RATE = 5  # Job weight 1–9; a hard cap would stall tests and builds
MAX_OUTPUT = 40_000
MAX_TIMEOUT = 600
_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
ENV_ALLOW = {
    "PATH", "PATHEXT", "SYSTEMROOT", "COMSPEC", "SYSTEMDRIVE", "WINDIR",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
    "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "TMP", "TEMP",
    # Без них ломаются pip --user, глобальный конфиг npm и модули PowerShell.
    "HOMEDRIVE", "HOMEPATH", "USERNAME", "PSMODULEPATH", "PROGRAMFILES",
    "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES", "HOME", "LANG",
}
ENV_BLOCK = {"MEMUAI_TOKEN", "MEMUAI_PROXY_URL", "OPENAI_API_KEY", "API_KEY"}

# Команда в позиции исполняемого файла: начало строки или после ; & | &&.
_CMD = r"(?:^|[;&|(]\s*)"
FORBIDDEN_PATTERNS = (
    r"\bformat(?:\.com)?\s+[a-z]:", _CMD + r"mkfs\b", _CMD + r"diskpart\b",
    _CMD + r"(?:shutdown|reboot|halt|poweroff)\b", _CMD + r"stop-computer\b", _CMD + r"restart-computer\b",
    r"\breg(?:\.exe)?\s+(add|delete)\b", _CMD + r"bcdedit\b", _CMD + r"vssadmin\b",
    r"\bnet\s+user\b", _CMD + r"netsh\b", r"\bsc\s+(config|delete)\b",
    r"\brm\s+-[a-z]*r[a-z]*f?[a-z]*\s+(?:/|~|\$home)(?:\s|$|/\*)",
    r"\bdel\s+(?:/[a-z]\s+)*[a-z]:\\?\s*(?:$|\*)", r"\bdel\s+/[sq]\s+[a-z]:\\",
    r"\bremove-item\b[^;&|]*-recurse[^;&|]*\s[a-z]:\\?(?:\s|$|\*)",
    r"\bremove-item\b[^;&|]*\s[a-z]:\\?(?:\s|$|\*)[^;&|]*-recurse",
    r"\brd\s+/s\s+(?:/q\s+)?[a-z]:\\?\s*$", r":\(\)\s*\{.*\};:",
    r"\bchown\s+-R\s+/", r"\bchmod\s+-R\s+777\s+/",
    r"\bcurl\b[^|]*\|\s*(ba)?sh", r"\bwget\b[^|]*\|\s*(ba)?sh",
    r"\b(?:iwr|invoke-webrequest|irm|invoke-restmethod)\b[^|]*\|\s*(?:iex|invoke-expression)\b",
    r"\bgit\s+push\b.*--force", r"\bgit\s+reset\s+--hard\b.*origin",
)


def forbidden_reason(command: str) -> str:
    low = " ".join(str(command or "").lower().split())
    # Текст в кавычках — данные, а не команда: «git commit -m "fix shutdown"»
    # раньше считался выключением компьютера.
    unquoted = re.sub(r'"[^"]*"|\'[^\']*\'', '""', low)
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, unquoted):
            return pattern
    return ""


def legacy_codecs() -> List[str]:
    """Code pages a Windows console program may print in when it ignores the UTF-8 request: the OEM page (cp866 on
    a Russian Windows: cmd built-ins, ping, ipconfig, MSBuild) and the ANSI page (cp1251)."""
    pages: List[str] = []
    if sys.platform == "win32":
        try:
            import ctypes
            for getter in ("GetOEMCP", "GetACP"):
                page = int(getattr(ctypes.windll.kernel32, getter)())
                if page and page != 65001:
                    pages.append(f"cp{page}")
        except Exception:
            pages = ["cp866", "cp1251"]
    else:
        pages = ["cp1251"]
    out: List[str] = []
    for page in pages:
        try:
            codecs.lookup(page)
        except LookupError:
            continue
        if page not in out:
            out.append(page)
    return out or ["cp1251"]


def _letters(text: str) -> int:
    return sum(1 for char in text if "\u0410" <= char <= "\u044f" or char in "\u0401\u0451" or char.isascii() and char.isalpha())


class OutputDecoder:
    """Command output bytes → text. Valid UTF-8 stays UTF-8; a line that is not UTF-8 is read in the legacy code page
    whose reading looks most like words (cp866 vs cp1251 decided by Cyrillic letters, then kept), so Russian
    compiler and system messages are readable instead of "\ufffd\ufffd\ufffd" — even when git prints UTF-8 and the
    compiler cp866 into the same stream. A multi-byte character split between two reads waits for the next one."""

    def __init__(self) -> None:
        self.carry = b""
        self.codec = ""                      # the legacy page, once one was needed

    def _legacy(self, raw: bytes) -> str:
        if not self.codec:
            best, best_score = "", -1
            for page in legacy_codecs():
                score = _letters(raw.decode(page, "replace"))
                if score > best_score:
                    best, best_score = page, score
            self.codec = best or "cp1251"
        return self.codec

    def decode(self, data: bytes, final: bool = False) -> str:
        raw, self.carry = self.carry + data, b""
        out: List[str] = []
        while raw:
            try:
                out.append(raw.decode("utf-8"))
                break
            except UnicodeDecodeError as exc:
                out.append(raw[:exc.start].decode("utf-8"))
                if exc.reason == "unexpected end of data" and not final and len(raw) - exc.start <= 3:
                    self.carry = raw[exc.start:]
                    break
                newline = raw.find(b"\n", exc.start)
                end = len(raw) if newline < 0 else newline + 1
                segment = raw[exc.start:end]
                out.append(segment.decode(self._legacy(segment), "replace"))
                raw = raw[end:]
        return "".join(out)


class NewlineJoiner:
    """CRLF → LF across read chunks. A chunk may end between "\r" and "\n";
    replacing per chunk then left a stray "\r" in the output (Windows tools),
    which made lines overwrite each other in the terminal and in the model's
    view. A lone trailing "\r" waits for the next chunk."""

    def __init__(self) -> None:
        self.carry = ""

    def feed(self, text: str, final: bool = False) -> str:
        text, self.carry = self.carry + text, ""
        if not final and text.endswith("\r"):
            text, self.carry = text[:-1], "\r"
        return text.replace("\r\n", "\n")


def kill_tree(process: "subprocess.Popen") -> None:
    """Убивает процесс вместе с детьми, не трогая остальные процессы сессии.

    process.kill() в Windows останавливает только cmd/powershell, а node от
    «npm run dev» продолжал держать порт.
    """
    if sys.platform == "win32":
        if process.poll() is not None:
            return
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                           capture_output=True, timeout=15, creationflags=_CREATE_NO_WINDOW)
        except Exception:
            pass
    else:
        # Every shell here starts with start_new_session=True, so its process
        # group id is its pid. The group is killed even when the shell itself
        # already exited: «server &» or «nohup …» left orphans that kept ports
        # and files busy, because the old early return on poll() skipped them.
        # The kernel does not reuse a pid while it still names a live group.
        try:
            import signal
            os.killpg(process.pid, signal.SIGKILL)
        except Exception:
            pass
        if process.poll() is not None:
            return
    try:
        process.kill()
    except OSError:
        pass


def run_bounded(argv: list, *, input_bytes: bytes = b"", cwd: Optional[str] = None,
                env: Optional[Dict[str, str]] = None, timeout: float = 60.0):
    """Run a short helper command (hook, status line) with a hard time limit.

    subprocess.run(timeout=…) kills only the direct child and then waits for
    the pipes without a limit; a grandchild that keeps stdout open (a hook that
    starts «npm run lint», a status line script that spawns git) made the
    caller hang forever on Windows. Here the whole tree is killed and the final
    read is bounded too. Returns (exit_code, stdout, stderr, timed_out); the
    exit code is None after a timeout.
    """
    flags = _CREATE_NO_WINDOW if sys.platform == "win32" else 0
    extra = {} if sys.platform == "win32" else {"start_new_session": True}
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               cwd=cwd, env=env, creationflags=flags, **extra)
    try:
        out, err = process.communicate(input_bytes, timeout=max(0.1, float(timeout)))
        return process.returncode, out or b"", err or b"", False
    except subprocess.TimeoutExpired:
        kill_tree(process)
        try:
            out, err = process.communicate(timeout=3)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            out, err = b"", b""              # something still holds the pipes: give up on the output
        return None, out or b"", err or b"", True
    except BaseException:
        kill_tree(process)
        raise


def utf8_argv(argv: list) -> list:
    """Вывод консольных программ Windows — в OEM-кодировке (cp866), а читаем мы
    UTF-8: русские сообщения компиляторов и git превращались в кракозябры."""
    if sys.platform != "win32" or len(argv) < 2:
        return argv
    head = str(argv[0]).lower()
    if head.startswith("powershell") or head.startswith("pwsh"):
        if "-Command" in argv:
            index = argv.index("-Command") + 1
            if index < len(argv):
                argv = list(argv)
                argv[index] = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
                               "$OutputEncoding=[Text.Encoding]::UTF8; " + str(argv[index]))
    elif head in ("cmd", "cmd.exe") and len(argv) >= 3 and str(argv[1]).lower() == "/c":
        argv = [argv[0], argv[1], "chcp 65001>nul & " + str(argv[2])] + list(argv[3:])
    return argv


def filtered_env(session_id: str, root: Path) -> Dict[str, str]:
    env = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper not in ENV_ALLOW or upper in ENV_BLOCK:
            continue
        if re.search(r"key|token|secret|pass|auth|cookie|credential", key, re.I):
            continue
        env[key] = value
    temp = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "MemuAI" / "code-temp" / str(session_id)[:64]
    temp.mkdir(parents=True, exist_ok=True)
    env["TMP"] = str(temp)
    env["TEMP"] = str(temp)
    env["MEMUAI_CODE_SESSION"] = str(session_id)[:64]
    env["MEMUAI_PROJECT_ROOT"] = str(root)
    env.setdefault("NO_COLOR", "1")
    if sys.platform == "win32":
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
    return env


def _windows_kernel32():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


class SessionJob:
    """One Job Object. On Windows it is a real kernel job; elsewhere it is a
    lightweight process registry so the same API is testable everywhere."""

    def __init__(self, session_id: str, root: Path, *,
                 memory_limit: int = DEFAULT_MEMORY_LIMIT,
                 cpu_rate: int = DEFAULT_CPU_RATE) -> None:
        self.session_id = str(session_id)[:64]
        self.root = Path(root).resolve()
        self.memory_limit = int(memory_limit)
        self.cpu_rate = int(cpu_rate)
        self.created = time.time()
        self.processes: set[subprocess.Popen] = set()
        self._lock = threading.Lock()
        self._handle = None
        self.closed = False
        self._create_job()

    def _create_job(self) -> None:
        if sys.platform != "win32":
            return
        try:
            kernel = _windows_kernel32()
            handle = kernel.CreateJobObjectW(None, None)
            if not handle:
                raise OSError("Windows Job Object could not be created")
            try:
                self._apply_limits(handle)
            except BaseException:
                kernel.CloseHandle(handle)
                raise
            self._handle = handle
        except Exception as exc:
            raise RuntimeError("MemuCode cannot start a limited process") from exc

    def _apply_limits(self, handle) -> None:
        import ctypes
        from ctypes import wintypes

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(ctypes.Structure):
            _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]

        # Active processes, job/process memory, kill children when the job closes.
        # Do not set a CPU-time limit of zero: that flag kills the process immediately.
        value = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        value.BasicLimitInformation.LimitFlags = 0x00000008 | 0x00000100 | 0x00000200 | 0x00002000
        value.BasicLimitInformation.ActiveProcessLimit = 32
        value.JobMemoryLimit = self.memory_limit
        value.ProcessMemoryLimit = self.memory_limit
        kernel = _windows_kernel32()
        if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(value), ctypes.sizeof(value)):
            raise OSError("Windows Job Object memory limits could not be applied")
        cpu = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION()
        cpu.ControlFlags = 0x1 | 0x2  # enable, weight-based
        cpu.CpuRate = max(1, min(9, int(self.cpu_rate or 5)))
        if not kernel.SetInformationJobObject(handle, 15, ctypes.byref(cpu), ctypes.sizeof(cpu)):
            raise OSError("Windows Job Object CPU limit could not be applied")

    def track(self, process: subprocess.Popen) -> bool:
        with self._lock:
            self.processes.add(process)
            handle = self._handle
        if not (handle and sys.platform == "win32" and process.pid):
            return False
        try:
            kernel = _windows_kernel32()
            # PROCESS_SET_QUOTA | PROCESS_TERMINATE, the rights AssignProcessToJobObject requires.
            process_handle = kernel.OpenProcess(0x0100 | 0x0001, False, int(process.pid))
            if not process_handle:
                return False
            assigned = bool(kernel.AssignProcessToJobObject(handle, process_handle))
            kernel.CloseHandle(process_handle)
            return assigned
        except OSError:
            return False

    def forget(self, process: subprocess.Popen) -> None:
        with self._lock:
            self.processes.discard(process)

    def kill_all(self) -> int:
        with self._lock:
            processes = list(self.processes)
            self.processes.clear()
            handle = self._handle
        killed = 0
        for process in processes:
            if sys.platform != "win32":
                # Without a kernel job only the process group reaches the
                # children: process.kill() stopped the shell and left
                # «npm run dev» and its node server running after the session.
                if process.poll() is None:
                    killed += 1
                kill_tree(process)
                continue
            if process.poll() is None:
                try:
                    process.kill()
                    killed += 1
                except OSError:
                    pass
        if handle and sys.platform == "win32":
            try:
                _windows_kernel32().TerminateJobObject(handle, 1)
            except OSError:
                pass
        return killed

    def close(self) -> None:
        self.closed = True
        self.kill_all()
        handle, self._handle = self._handle, None
        if handle and sys.platform == "win32":
            try:
                _windows_kernel32().CloseHandle(handle)
            except OSError:
                pass

    def status(self) -> Dict[str, Any]:
        alive = []
        with self._lock:
            for process in self.processes:
                code = process.poll()
                if code is None:
                    alive.append({"pid": process.pid})
        return {"session": self.session_id, "active_processes": len(alive),
                "processes": alive[:20], "memory_limit": self.memory_limit,
                "cpu_rate": self.cpu_rate, "created": self.created, "closed": self.closed}


class SandboxManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, SessionJob] = {}
        self._lock = threading.Lock()

    def job(self, session_id: str, root: str | Path, *, create: bool = True) -> Optional[SessionJob]:
        session_id = str(session_id or "default")[:64]
        resolved = Path(root).resolve()
        with self._lock:
            job = self._jobs.get(session_id)
            if job is None or job.closed:
                if not create:
                    return None
                job = SessionJob(session_id, resolved)
                self._jobs[session_id] = job
            return job

    def close(self, session_id: str) -> None:
        with self._lock:
            job = self._jobs.pop(str(session_id or ""), None)
        if job:
            job.close()

    def close_all(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
            self._jobs.clear()
        for job in jobs:
            job.close()

    def status(self, session_id: str) -> Dict[str, Any]:
        with self._lock:
            job = self._jobs.get(str(session_id or ""))
        return job.status() if job else {"session": str(session_id), "active_processes": 0, "closed": True}


def _popen(argv: list, cwd: str, env: Dict[str, str], *, suspended: bool) -> subprocess.Popen:
    flags = 0
    if sys.platform == "win32":
        flags = _CREATE_NO_WINDOW
        if suspended:
            flags |= _CREATE_SUSPENDED
    extra = {} if sys.platform == "win32" else {"start_new_session": True}
    env = dict(env or {})
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # Bytes, not text=True: a program that ignores the UTF-8 request prints cp866/cp1251, and a strict
    # UTF-8 reading turned its Russian messages into "\ufffd". read_text() decodes like the CLI does.
    process = subprocess.Popen(
        utf8_argv(argv), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags, **extra,
    )
    process._memu_suspended = bool(suspended and sys.platform == "win32")  # type: ignore[attr-defined]
    return process


def _resume(process: subprocess.Popen) -> bool:
    if not getattr(process, "_memu_suspended", False):
        return True
    handle = getattr(process, "_handle", None)
    if not handle:
        return False
    try:
        import ctypes
        status = ctypes.windll.ntdll.NtResumeProcess(ctypes.c_void_p(int(handle)))
        return int(status) == 0
    except Exception:
        return False


def _discard_process(job: SessionJob, process: subprocess.Popen) -> None:
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    for pipe in (process.stdout, process.stderr):
        if pipe is not None:
            pipe.close()
    job.forget(process)


def spawn(session_id: str, root: str | Path, argv: list, *, cwd: str,
          env: Optional[Dict[str, str]] = None) -> subprocess.Popen:
    """Start a process inside the session job. Children die with the job."""
    job = manager.job(session_id, root)
    if job is None:
        raise RuntimeError("sandbox is closed")
    environment = env or filtered_env(session_id, Path(root))
    suspended = sys.platform == "win32"
    process = _popen(argv, cwd, environment, suspended=suspended)
    if suspended and not job.track(process):
        _discard_process(job, process)
        raise RuntimeError("MemuCode could not attach the process to its Windows Job Object")
    if not suspended:
        job.track(process)
    if suspended and not _resume(process):
        _discard_process(job, process)
        raise RuntimeError("MemuCode could not resume its limited process")
    return process


def read_text(pipe, on_text, size: int = 8192) -> None:
    """Read a binary pipe of a sandboxed process to its end, handing over decoded text with LF line ends."""
    decoder, newlines = OutputDecoder(), NewlineJoiner()
    try:
        while chunk := (pipe.read1(size) if hasattr(pipe, "read1") else pipe.read(size)):
            text = newlines.feed(decoder.decode(chunk))
            if text:
                on_text(text)
    finally:
        tail = newlines.feed(decoder.decode(b"", final=True), final=True)
        if tail:
            on_text(tail)


def _drain(pipe) -> str:
    try:
        if pipe is None:
            return ""
        parts = [""]

        def keep(text: str) -> None:
            parts[0] = (parts[0] + text)[-MAX_OUTPUT:]

        read_text(pipe, keep)
        return parts[0]
    except Exception:
        return ""
    finally:
        if pipe is not None:
            try:
                pipe.close()
            except Exception:
                pass


def launch_captured(session_id: str, root: str | Path, argv: list, *, cwd: str,
                    timeout: int, env: Optional[Dict[str, str]] = None,
                    on_start=None) -> Dict[str, Any]:
    """Run argv to completion inside the session job and return its output."""
    timeout = max(1, min(MAX_TIMEOUT, int(timeout or 30)))
    process = spawn(session_id, root, argv, cwd=cwd, env=env)
    if on_start is not None:
        on_start(process)
    stdout_box: list[str] = []
    stderr_box: list[str] = []
    threads = [
        threading.Thread(target=lambda: stdout_box.append(_drain(process.stdout)), daemon=True),
        threading.Thread(target=lambda: stderr_box.append(_drain(process.stderr)), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        # Только дерево этой команды: фоновые задачи сессии (dev-сервер,
        # watcher из code_task) не должны умирать из-за чужого таймаута.
        kill_tree(process)
        try:
            code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            code = -1
    for thread in threads:
        thread.join(2)
    job = manager.job(session_id, root, create=False)
    if job is not None:
        job.forget(process)
    if timed_out:
        raise TimeoutError(f"command exceeded {timeout}s and was stopped")
    return {
        "exit_code": code,
        "stdout": (stdout_box[0] if stdout_box else "")[-MAX_OUTPUT:],
        "stderr": (stderr_box[0] if stderr_box else "")[-MAX_OUTPUT:],
    }


manager = SandboxManager()
