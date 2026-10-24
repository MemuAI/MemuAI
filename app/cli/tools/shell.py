"""Shell tools: Bash (foreground or background), BashOutput and KillShell.

Commands run inside the MemuCode sandbox of the session — one Windows Job
Object with a memory limit whose whole process tree dies on Escape, timeout or
exit (:mod:`app.code_sandbox`). Output is streamed to the terminal live and
bounded for the model (head and tail are kept).

On Windows the shell is Git Bash when it is installed (models write bash far
more reliably), otherwise PowerShell; ``shell`` in the settings overrides it.
"""
from __future__ import annotations

import asyncio
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ... import code_sandbox
from ..i18n import tr
from .base import Prepared, Tool, ToolContext, ToolError, ToolResult, arg, flag, workspace_of

MAX_OUTPUT_CHARS = 20_000         # Round 32: was 30 000 (head 12 000) — the tail is what matters
HEAD_CHARS = 5_000
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600
SECRET_ENV = re.compile(r"key|token|secret|passw|auth|cookie|credential|private", re.I)


# ---------------------------------------------------------------- shells

def find_git_bash() -> Optional[str]:
    override = os.environ.get("MEMUCODE_GIT_BASH", "").strip()
    if override and Path(override).is_file():
        return override
    if os.name != "nt":
        return None
    candidates = []
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if base:
            candidates.append(Path(base) / "Git" / "bin" / "bash.exe")
    git = shutil.which("git")
    if git:
        root = Path(git).resolve().parent.parent
        candidates += [root / "bin" / "bash.exe", root.parent / "bin" / "bash.exe"]
    for path in candidates:
        if path.is_file() and "system32" not in str(path).lower():
            return str(path)
    return None


def shell_spec(settings: Any) -> Tuple[str, str]:
    """(kind, executable): bash | powershell | pwsh | cmd | sh."""
    wanted = str(settings.get("shell") or "auto").lower()
    if os.name != "nt":
        if wanted in ("powershell", "pwsh") and shutil.which("pwsh"):
            return "pwsh", shutil.which("pwsh") or "pwsh"
        return "bash", shutil.which("bash") or "/bin/sh"
    if wanted in ("auto", "bash"):
        bash = find_git_bash()
        if bash:
            return "bash", bash
    if wanted == "pwsh" and shutil.which("pwsh"):
        return "pwsh", shutil.which("pwsh") or "pwsh"
    if wanted == "cmd":
        return "cmd", "cmd"
    return "powershell", "powershell"


def shell_label(settings: Any) -> str:
    kind, _exe = shell_spec(settings)
    return {"bash": "bash (Git Bash)" if os.name == "nt" else "bash", "powershell": "Windows PowerShell 5.1",
            "pwsh": "PowerShell 7", "cmd": "cmd.exe"}.get(kind, kind)


def argv_for(settings: Any, command: str) -> List[str]:
    kind, exe = shell_spec(settings)
    if kind == "bash":
        return [exe, "-c", command]
    if kind in ("powershell", "pwsh"):
        return code_sandbox.utf8_argv([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                       "-Command", command])
    return code_sandbox.utf8_argv(["cmd", "/c", command])


def command_env(runtime: Any) -> Dict[str, str]:
    mode = str(runtime.settings.get("sandbox.env") or "filtered")
    if mode == "strict":
        env = code_sandbox.filtered_env(runtime.session.id, runtime.root)
    else:
        env = dict(os.environ)
        if mode == "filtered":
            env = {key: value for key, value in env.items()
                   if key.upper() not in code_sandbox.ENV_BLOCK and not SECRET_ENV.search(key)}
    for key, value in (runtime.settings.get("env") or {}).items():
        env[str(key)] = str(value)
    env.update({"MEMUCODE": "1", "MEMUAI_PROJECT_ROOT": str(runtime.root), "GIT_TERMINAL_PROMPT": "0",
                "GIT_PAGER": "cat", "PAGER": "cat", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
                "NO_COLOR": "1", "FORCE_COLOR": "0"})
    if os.name == "nt":
        env.setdefault("PYTHONUTF8", "1")
    return env


# ---------------------------------------------------------------- command analysis

def split_commands(command: str) -> List[str]:
    """``a && b | c; d`` → [a, b, c, d]; operators inside quotes are ignored."""
    parts: List[str] = []
    current: List[str] = []
    quote = ""
    index = 0
    text = str(command or "")
    while index < len(text):
        char = text[index]
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            elif char == "\\" and quote == '"' and index + 1 < len(text):
                current.append(text[index + 1])
                index += 1
            index += 1
            continue
        if char in "\"'":
            quote = char
            current.append(char)
        elif text.startswith("&&", index) or text.startswith("||", index):
            parts.append("".join(current))
            current = []
            index += 1
        elif char == "&" and ((current and current[-1] == ">") or text.startswith(">", index + 1)):
            current.append(char)              # 2>&1 and &> are redirections, not "run in background"
        elif char in ";|\n&":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


_READ_ONLY = [re.compile(pattern, re.I) for pattern in (
    r"git (status|diff|log|show|rev-parse|ls-files|blame|describe|shortlog|reflog|ls-tree|cat-file|grep|"
    r"remote(?: -v| show [\w.-]+)?$|branch(?: (?:-a|-r|-v|-vv|--list|--show-current))*$|tag(?: -l| --list)?$|"
    r"config --get|stash list|worktree list|check-ignore|merge-base|name-rev)\b",
    r"(ls|dir|pwd|cat|type|head|tail|wc|echo|printf|which|where|whoami|date|uname|hostname|tree|stat|file|"
    r"du|df|sort|uniq|cut|tr|basename|dirname|realpath|readlink|nl|od|hexdump|md5sum|sha1sum|sha256sum|"
    r"true|test|printenv|id|groups|nproc|free|uptime|ps|column|diff|cmp|comm|jq|yq)(\s|$)",
    r"(rg|grep|egrep|fgrep|ag|ack)(\s|$)",
    r"env\s*$",
    r"find\s(?!.*\s-(delete|exec|execdir|ok|okdir|fprint|fprintf|fls)\b)",
    r"sed\s+-n\s", r"awk\s(?!.*(system|>|print\s*>))",
    r"(python3?|py|node|npm|npx|pnpm|yarn|pip3?|cargo|go|java|javac|dotnet|rustc|ruby|php|deno|bun|uv|"
    r"poetry|mvn|gradle|tsc|gcc|clang|cmake|make|docker|kubectl|terraform)\s+(--version|-v|-V|version)\s*$",
    r"(npm|pnpm|yarn) (ls|list|view|info|outdated|why|config get|root|bin|prefix)\b",
    r"(pip3?|python3? -m pip) (list|show|freeze|check|index versions)\b",
    r"(go (list|env|version|doc)|cargo (tree|metadata|--list)|dotnet (--info|--list-sdks|list)|"
    r"docker (ps|images|version|info)|kubectl (get|describe|version)|terraform (show|plan|validate|version))\b",
    r"(get-childitem|gci|get-content|gc|select-string|sls|get-location|gl|test-path|get-item|gi|"
    r"resolve-path|measure-object|get-command|gcm|get-process|gps|get-date|get-host|format-table|ft|"
    r"format-list|fl|select-object|select|where-object|where|sort-object|group-object|out-string|"
    r"convertto-json|convertfrom-json|write-output|write-host|split-path|join-path|get-filehash)\b",
    r"(nvidia-smi|systeminfo|ver|vol|tasklist|findstr|more|ipconfig)(\s|$)",
)]
_WRITE_MARKS = re.compile(r"(?<![0-9&])>{1,2}(?!&)|\$\(|`|\btee\b|\bxargs\b|-delete\b|\bsed\s+-i|"
                          r"\bout-file\b|\bset-content\b|\badd-content\b|\bremove-item\b|\bnew-item\b|"
                          r"\bcopy-item\b|\bmove-item\b|\brename-item\b|\binvoke-expression\b|\biex\b")


#: Redirections that only merge streams or discard output (``2>&1``, ``2>/dev/null``, ``>NUL``):
#: models append them to almost every command, and they neither write a file nor expand anything.
_HARMLESS_REDIRECT = re.compile(r"(?<![\w&>])\d?(?:>&\d|>>?\s*(?:/dev/null|nul)(?![\w./\\:-]))", re.I)


def _strip_harmless_redirects(command: str) -> str:
    return _HARMLESS_REDIRECT.sub(" ", str(command or ""))


def is_static_command(command: str) -> bool:
    """Whether shell syntax can be analysed without executing an expansion.

    Double quotes still expand Bash/PowerShell expressions. Parenthesised and
    script-block arguments also execute code in PowerShell; cmd expands %VAR%
    even inside quotes. Unknown syntax must reach the permission dialog.
    """
    text = _strip_harmless_redirects(command)
    if re.search(r"%[^%\r\n]+%", text):
        return False
    quote = ""
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
            elif quote == '"' and char in "$`":
                return False
            elif quote == '"' and char == "\\" and index + 1 < len(text):
                # Escapes differ between Bash and PowerShell: a backslash may
                # not hide an expansion from the shared safety classifier.
                if text[index + 1] in "$`\"":
                    return False
                index += 1
        elif char in "\"'":
            quote = char
        elif char in "$`<>^(){}[]@":
            return False
        index += 1
    return not quote


def _mutating_read_options(part: str) -> bool:
    """Read-oriented executables can still write or launch another program."""
    try:
        words = shlex.split(part, posix=True)
    except ValueError:
        return True
    if not words:
        return True
    head = words[0].lower()
    options = words[1:]
    lowered = [word.lower() for word in options]

    def short_has(word: str, flags: str) -> bool:
        # Short switches may be grouped and an argument may be attached. An
        # ambiguous group asks first rather than assuming an output flag is data.
        return word.startswith("-") and not word.startswith("--") and any(flag in word[1:] for flag in flags)

    def long_has(word: str, names: Tuple[str, ...]) -> bool:
        return any(word == name or word.startswith(name + "=") for name in names)

    if head == "env":                       # env executes its positional command
        return bool(options)
    if head == "git":
        if any(long_has(word, ("--ext-diff", "--textconv", "--filters", "--output")) for word in lowered):
            return True
        if lowered[:1] == ["reflog"] and lowered[1:2] in (["expire"], ["delete"], ["drop"], ["write"]):
            return True
        if lowered[:1] == ["grep"]:
            return any(long_has(word, ("--open-files-in-pager",)) or short_has(word, "O") for word in options[1:])
        return False
    if head in ("rg", "ag", "ack"):
        return any(word in ("--pre", "--hostname-bin", "--pager")
                   or word.startswith(("--pre=", "--hostname-bin=", "--pager=")) for word in lowered)
    if head in ("sort", "yq", "tree"):
        names = ("--output", "--in-place", "--inplace", "--compress-program", "--split-exp")
        return any(long_has(word, names) or short_has(word, "ois" if head == "yq" else "o") for word in options)
    if head == "find":
        return any(long_has(word, ("-delete", "-exec", "-execdir", "-ok", "-okdir",
                                   "-fprint", "-fprint0", "-fprintf", "-fls")) for word in options)
    if head == "file":
        return any(long_has(word, ("--compile",)) or short_has(word, "C") for word in options)
    if head == "printf":
        return any(short_has(word, "v") for word in options)
    if head == "hostname":
        long_flags = {"--alias", "--all-fqdns", "--domain", "--fqdn", "--long", "--ip-address",
                      "--all-ip-addresses", "--short", "--nis", "--yp", "--version", "--help"}
        return any(word not in long_flags and word != "/?"
                   and not re.fullmatch(r"-[aAdfIisyVh?]+", word) for word in options)
    if head == "uniq":
        # The second operand is an output file. Unknown switches may consume
        # arguments, so only the ordinary flag forms are accepted here.
        flags = re.compile(r"^-(?:[cduizD]+|[fsw]\d+)$|^--(?:count|repeated|unique|ignore-case|zero-terminated)$")
        return (any(word.startswith("-") and not flags.fullmatch(word) for word in options)
                or len([word for word in options if not word.startswith("-")]) > 1)
    if head == "date":
        # A bare numeric operand sets the clock. -d/--date and -r/--reference
        # consume a read-only argument; +FORMAT only controls stdout.
        consume = False
        for word in options:
            if consume:
                consume = False
                continue
            if word in ("-d", "--date", "-r", "--reference"):
                consume = True
            elif word.startswith(("--date=", "--reference=", "+")):
                continue
            elif word.startswith("--"):
                if not (word in ("--utc", "--universal", "--rfc-email", "--debug", "--help", "--version")
                        or long_has(word, ("--iso-8601", "--rfc-3339"))):
                    return True
            elif word.startswith("-"):
                if word.startswith(("-d", "-r")):
                    continue
                if not re.fullmatch(r"-[uUR]+|-I(?:date|hours|minutes|seconds|ns)?", word):
                    return True
            else:
                return True
        return False
    if head == "terraform":
        return any(long_has(word, ("-out", "-generate-config-out")) for word in options)
    if head == "go" and lowered[:1] == ["env"]:
        return any(word in ("-w", "-u") for word in options[1:])
    if head == "ipconfig":
        return any(word not in ("/all", "/allcompartments", "/displaydns", "/?") for word in lowered)
    if head == "nvidia-smi":
        return any(not (word in ("-L", "-q", "--list-gpus", "--query", "--help", "--version")
                        or word.startswith(("--query-", "--format="))) for word in options)
    if head in ("sed", "awk"):
        # Both languages can write files or execute code in quoted programs.
        # Keep the common sed line-window read; other programs ask first.
        if not (head == "sed" and len(words) >= 3 and words[1] == "-n"
                and re.fullmatch(r"\d+(?:,\d+|,\$)?p", words[2])):
            return True
        # A later -e changes the program; -i writes even when the first program
        # only prints. After this program accept only filenames (or -- files).
        files = words[3:]
        return bool(files and files[0] != "--" and any(word.startswith("-") for word in files))
    return False


def is_read_only(command: str) -> bool:
    text = _strip_harmless_redirects(command).strip()
    if not text or not is_static_command(text) or _WRITE_MARKS.search(re.sub(r"'[^']*'|\"[^\"]*\"", "''", text)):
        return False
    for part in split_commands(text):
        part = " ".join(part.split())
        # Environment assignments can configure loaders and execute code even
        # when the visible executable normally only reads.
        if re.match(r"^\w+=", part) or _mutating_read_options(part):
            return False
        part = re.sub(r"^cd\s+\S+$", "true", part)
        if part in ("true", ":"):
            continue
        if not any(pattern.match(part) for pattern in _READ_ONLY):
            return False
    return True


def rule_prefix(command: str) -> str:
    """Suggested ``Bash(prefix:*)`` for "don't ask again": ``npm test``, ``git commit``."""
    first = split_commands(command)[0] if split_commands(command) else command
    try:
        words = shlex.split(first, posix=True)
    except ValueError:
        words = first.split()
    words = [word for word in words if not re.match(r"^\w+=", word)] or words
    if not words:
        return command.strip()
    head = words[0]
    two = {"git", "npm", "pnpm", "yarn", "cargo", "go", "docker", "kubectl", "dotnet", "pip", "pip3", "uv",
           "poetry", "make", "npx", "bun", "deno", "gradle", "mvn", "terraform", "gh", "brew", "winget",
           "choco", "composer", "bundle", "rails", "php", "flutter", "dart", "swift", "nx", "turbo"}
    if head in ("python", "python3", "py") and len(words) >= 3 and words[1] == "-m":
        return " ".join(words[:3])
    if head in two and len(words) >= 2 and not words[1].startswith("-"):
        if head == "npm" and words[1] == "run" and len(words) >= 3:
            return " ".join(words[:3])
        return " ".join(words[:2])
    return head


# ---------------------------------------------------------------- runner

def _spawn(runtime: Any, argv: List[str], cwd: Path, env: Dict[str, str]) -> subprocess.Popen:
    sandboxed = bool(runtime.settings.get("sandbox.enabled", True))
    job = code_sandbox.manager.job(runtime.session.id, runtime.root) if sandboxed else None
    flags = 0
    extra: Dict[str, Any] = {}
    if sys.platform == "win32":
        flags = 0x08000000                              # CREATE_NO_WINDOW
        if job is not None:
            flags |= 0x00000004                         # CREATE_SUSPENDED until assigned to the job
    else:
        extra["start_new_session"] = True
    try:
        process = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags,
                                   **extra)
    except OSError as exc:
        raise ToolError(f"Could not start the shell ({argv[0]}): {exc}") from exc
    if job is not None:
        process._memu_suspended = sys.platform == "win32"  # type: ignore[attr-defined]
        if sys.platform == "win32" and not job.track(process):
            code_sandbox._discard_process(job, process)
            raise ToolError("The command could not be attached to the MemuCode sandbox (Windows Job Object).")
        if sys.platform != "win32":
            job.track(process)
        if not code_sandbox._resume(process):
            code_sandbox._discard_process(job, process)
            raise ToolError("The sandboxed command could not be resumed.")
    return process


class _Collector:
    """Keeps the head and the tail of a possibly huge output.

    Round 32: the model gets a shorter head (errors are at the end) and, when the output is cut, the whole
    output is kept in a file it can Read in parts — a "see more" handle instead of a lost middle."""

    SPOOL_LIMIT = 20 * 1024 * 1024

    def __init__(self) -> None:
        self.head: List[str] = []
        self.head_size = 0
        self.tail = ""
        self.total = 0
        self.early: List[str] = []         # everything until the output first overflows
        self.spool: Any = None
        self.spool_path = ""
        self.spooled = 0

    def add(self, text: str) -> None:
        self.total += len(text)
        if self.spool is None and self.early is not None:
            self.early.append(text)
            if self.total > MAX_OUTPUT_CHARS:
                self._open_spool()
        elif self.spool is not None and self.spooled < self.SPOOL_LIMIT:
            self._write(text)
        if self.head_size < HEAD_CHARS:
            take = text[:HEAD_CHARS - self.head_size]
            self.head.append(take)
            self.head_size += len(take)
            text = text[len(take):]
        if text:
            self.tail = (self.tail + text)[-(MAX_OUTPUT_CHARS - HEAD_CHARS):]

    def _open_spool(self) -> None:
        early, self.early = self.early, None
        try:
            from ..paths import outputs_dir
            folder = outputs_dir()
            self._prune(folder)
            path = folder / f"bash-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{id(self) & 0xffff:04x}.log"
            self.spool = open(path, "w", encoding="utf-8", errors="replace", newline="")
            self.spool_path = str(path)
            for chunk in early or []:
                self._write(chunk)
        except OSError:
            self.spool = None
            self.spool_path = ""

    def _write(self, text: str) -> None:
        try:
            self.spool.write(text)
            self.spooled += len(text)
        except (OSError, ValueError):
            pass

    @staticmethod
    def _prune(folder: Any, keep: int = 60) -> None:
        try:
            files = sorted(folder.glob("bash-*.log"), key=lambda path: path.stat().st_mtime)
            for path in files[:-keep]:
                path.unlink()
        except OSError:
            pass

    def text(self) -> Tuple[str, bool]:
        head = "".join(self.head)
        if self.total <= MAX_OUTPUT_CHARS:
            return head + self.tail, False
        if self.spool is not None:
            try:
                self.spool.close()
            except OSError:
                pass
        omitted = self.total - len(head) - len(self.tail)
        see = (f" — the full output is saved in {self.spool_path}: Read it with offset/limit (or Grep it) if you "
               "need the middle" if self.spool_path else "")
        return f"{head}\n… [{omitted} characters omitted{see}] …\n{self.tail}", True


async def run_shell(runtime: Any, command: str, *, cwd: Path, timeout: int,
                    on_output: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    argv = argv_for(runtime.settings, command)
    process = await asyncio.to_thread(_spawn, runtime, argv, cwd, command_env(runtime))
    collector = _Collector()
    pending: List[str] = []
    lock = threading.Lock()

    def keep(text: str) -> None:
        with lock:
            collector.add(text)
            pending.append(text)

    def reader() -> None:
        try:
            code_sandbox.read_text(process.stdout, keep)
        except Exception:
            return

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    started = time.monotonic()
    deadline = started + max(1, min(MAX_TIMEOUT, timeout))
    timed_out = False

    def drain() -> None:
        if on_output is None:
            return
        with lock:
            chunk = "".join(pending)
            pending.clear()
        if chunk:
            on_output(chunk)

    try:
        while process.poll() is None:
            if time.monotonic() > deadline:
                timed_out = True
                await asyncio.to_thread(code_sandbox.kill_tree, process)
                break
            drain()
            await asyncio.sleep(0.06)
    except BaseException:
        # Escape (CancelledError) and any other failure here (an exception from
        # the progress callback) must not leave the command running unseen.
        await asyncio.to_thread(code_sandbox.kill_tree, process)
        raise
    await asyncio.to_thread(thread.join, 2.0)
    drain()
    if not thread.is_alive():
        try:
            process.stdout.close()
        except Exception:
            pass
    job = code_sandbox.manager.job(runtime.session.id, runtime.root, create=False)
    if job is not None:
        job.forget(process)
    output, truncated = collector.text()
    return {"exit_code": process.poll() if not timed_out else None, "output": output, "truncated": truncated,
            "timed_out": timed_out, "ms": int((time.monotonic() - started) * 1000)}


# ---------------------------------------------------------------- tools

def _timeout_seconds(value: Any) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    if number > 1000:                                   # Claude-style milliseconds
        number /= 1000
    return int(max(1, min(MAX_TIMEOUT, number)))


_CD = re.compile(r"^\s*cd\s+(?:\"([^\"]+)\"|'([^']+)'|([^\s;&|]+))\s*(?:&&|;|$)")


class BashTool(Tool):
    name = "Bash"
    kind = "exec"
    plan_safe = False
    short = "run a shell command in the project sandbox {command, timeout? (s), description?, run_in_background?}"
    parameters = {"type": "object", "properties": {
        "command": {"type": "string", "description": "The command to run"},
        "timeout": {"type": "number", "description": "Timeout in seconds (default 120, max 600)"},
        "description": {"type": "string", "description": "5-10 words on what the command does"},
        "run_in_background": {"type": "boolean",
                              "description": "Start independent work (tests, builds, servers) and return a job id immediately"},
    }}
    required = ("command",)

    @property
    def description(self) -> str:  # type: ignore[override]
        return ("Runs a command in the project's sandboxed shell (the shell named in the environment block — use its "
                "syntax) and returns combined stdout/stderr and the exit code; output over 30 000 characters keeps "
                "its head and tail. Use it for tests, builds, git, package managers. Prefer Read/Grep/Glob/Edit to "
                "cat/grep/find/sed. Quote paths that contain spaces. stdin is closed: interactive prompts fail, so "
                "pass --yes/--no-input style flags. A `cd` at the start of a command persists for later commands. "
                "timeout is in seconds (default 120, max 600). Servers, watchers and long suites: "
                "run_in_background=true, then BashOutput.")

    def permission_key(self, runtime, args):
        return str(arg(args, "command", "cmd", default="")).strip()

    def display(self, args):
        text = " ".join(str(arg(args, "command", "cmd", default="")).split())
        return text if len(text) <= 120 else text[:117] + "…"

    async def prepare(self, ctx, args):
        command = str(arg(args, "command", "cmd", default="")).strip()
        if not command:
            raise ToolError("Bash needs a non-empty command.")
        if code_sandbox.forbidden_reason(command):
            raise ToolError("This command is blocked by the MemuCode sandbox: it affects the whole machine "
                            "(disks, shutdown, system settings, force-push). Ask the user to run it manually.")
        cwd = Path(ctx.session.cwd or ctx.runtime.root)
        if not cwd.is_dir():
            cwd = ctx.runtime.root
        return Prepared(key=command, preview=command,
                        title=tr("Выполнить команду", "Run command"),
                        data={"command": command, "cwd": cwd, "read_only": is_read_only(command)})

    async def run(self, ctx, args, prepared):
        command = prepared.data["command"]
        cwd: Path = prepared.data["cwd"]
        runtime = ctx.runtime
        if flag(args.get("run_in_background")):
            return await self._background(ctx, command, cwd, args)
        timeout = _timeout_seconds(args.get("timeout"))
        result = await run_shell(runtime, command, cwd=cwd, timeout=timeout, on_output=ctx.progress)
        self._track_cd(ctx, command, cwd)
        output = result["output"].rstrip()
        seconds = result["ms"] / 1000
        if result["timed_out"]:
            status = tr(f"остановлено по таймауту {timeout} с", f"stopped after the {timeout}s timeout")
            tail = f"\n[timed out after {timeout}s; the process tree was stopped. For servers use run_in_background]"
            return ToolResult(ok=False, output=(output + tail).strip(), error=f"timeout after {timeout}s",
                              summary=status, detail=output[-4000:], meta={"exit_code": None})
        code = result["exit_code"]
        footer = f"\n[exit code {code} · {seconds:.1f}s]"
        if not output:
            output = "(no output)"
        summary = tr(f"код {code} · {seconds:.1f} с", f"exit {code} · {seconds:.1f}s")
        return ToolResult(ok=code == 0, output=output + footer, summary=summary, detail=output[-6000:],
                          error="" if code == 0 else f"exit code {code}",
                          meta={"exit_code": code, "truncated": result["truncated"], "ms": result["ms"]})

    def _track_cd(self, ctx: ToolContext, command: str, cwd: Path) -> None:
        match = _CD.match(command)
        if not match:
            return
        target = next(group for group in match.groups() if group)
        path = Path(os.path.expanduser(target))
        path = (cwd / path) if not path.is_absolute() else path
        try:
            path = path.resolve()
        except OSError:
            return
        if path.is_dir() and workspace_of(ctx.runtime, path) is not None:
            ctx.session.cwd = str(path)

    async def _background(self, ctx: ToolContext, command: str, cwd: Path, args: Dict[str, Any]) -> ToolResult:
        runtime = ctx.runtime
        row = await asyncio.to_thread(runtime.shells.start, runtime, command, cwd,
                                      str(args.get("description") or "")[:120])
        if getattr(ctx, "depth", 0):
            # Round 32: a sub-agent collects its own background command with BashOutput; its end is not news for
            # the main agent (it was woken by «Background command … finished» of a command it never started).
            row["announced"] = True
            row["owner_depth"] = int(ctx.depth)
        status = runtime.shells.read(row["id"])
        return ToolResult(ok=True, output=(f"Started in the background with id {row['id']}. "
                                           f"Check it with BashOutput {{\"bash_id\": \"{row['id']}\"}}, "
                                           f"stop it with KillShell.\nFirst output:\n"
                                           f"{status['new_output'][-3000:] or '(none yet)'}"),
                          summary=tr(f"Фоновая задача {row['id']}", f"Background task {row['id']}"),
                          meta={"background_id": row["id"]})


class BackgroundShells:
    """Long-running commands of a session (dev servers, watchers).

    They live in the same sandbox job as foreground commands, have no timeout
    and die with the session. Output is kept as a 64 KB ring; BashOutput
    returns only what appeared since the previous read.
    """

    RING = 64_000

    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()

    def start(self, runtime: Any, command: str, cwd: Path, label: str = "") -> Dict[str, Any]:
        running = [row for row in self.rows.values() if row["process"].poll() is None]
        if len(running) >= 8:
            raise ToolError("Eight background commands are already running; stop one with KillShell first.")
        process = _spawn(runtime, argv_for(runtime.settings, command), cwd, command_env(runtime))
        task_id = "bg" + os.urandom(3).hex()
        row = {"id": task_id, "command": command, "label": label, "started": time.time(), "process": process,
               "announced": False,
               "buffer": "", "total": 0, "read": 0, "exit_code": None, "cwd": str(cwd),
               "output_done": threading.Event()}
        self.rows[task_id] = row

        def keep(text: str) -> None:
            with self.lock:
                row["buffer"] = (row["buffer"] + text)[-self.RING:]
                row["total"] += len(text)

        def pump() -> None:
            try:
                code_sandbox.read_text(process.stdout, keep)
            except Exception:
                pass
            finally:
                process.stdout.close()
                row["exit_code"] = process.wait()
                row["finished"] = time.time()
                row["output_done"].set()

        threading.Thread(target=pump, daemon=True).start()
        return row

    async def wait(self, task_id: str, seconds: float) -> Dict[str, Any]:
        """Wait without occupying the event loop or consuming output on cancellation.

        Include the pump's final bytes before reporting completion. A timeout only
        stops waiting; the command continues until KillShell or session shutdown.
        """
        row = self.rows.get(task_id)
        if row is None:
            raise KeyError(task_id)
        deadline = time.monotonic() + seconds
        while not row["output_done"].is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.025, remaining))
        return self.read(task_id)

    def read(self, task_id: str) -> Dict[str, Any]:
        row = self.rows.get(task_id)
        if row is None:
            raise KeyError(task_id)
        with self.lock:
            available = row["total"] - row["read"]
            new = row["buffer"][-available:] if available > 0 else ""
            if available > len(row["buffer"]):
                new = f"… [{available - len(row['buffer'])} earlier characters dropped]\n" + row["buffer"]
            row["read"] = row["total"]
        exit_code = row["process"].poll()
        running = exit_code is None or not row["output_done"].is_set()
        if not running:
            row["announced"] = True              # the model has now seen how it ended: no reminder later
        return {"id": task_id, "command": row["command"],
                "running": running,
                "exit_code": exit_code, "new_output": new, "label": row["label"],
                "started": row["started"]}

    def kill(self, task_id: str) -> Dict[str, Any]:
        row = self.rows.get(task_id)
        if row is None:
            raise KeyError(task_id)
        code_sandbox.kill_tree(row["process"])
        return {"id": task_id, "command": row["command"]}

    def list(self) -> List[Dict[str, Any]]:
        return [{"id": row["id"], "command": row["command"], "label": row["label"], "started": row["started"],
                 "running": row["process"].poll() is None, "exit_code": row["process"].poll(),
                 "tail": row["buffer"][-400:]} for row in self.rows.values()]

    def running(self) -> int:
        return sum(1 for row in self.rows.values() if row["process"].poll() is None)

    def kill_all(self) -> None:
        # kill_tree itself skips finished processes where that is right; on
        # POSIX it still reaches children a finished shell left behind.
        for row in list(self.rows.values()):
            code_sandbox.kill_tree(row["process"])


class BashOutputTool(Tool):
    name = "BashOutput"
    kind = "read"
    parallel = True
    short = "poll or wait for a background command {bash_id, wait_seconds? (0-60)}"
    description = ("Returns whether a background command is still running, its exit code and the output "
                   "produced since the previous check. Without bash_id, lists background commands. "
                   "Poll immediately by default; wait_seconds waits up to 60 seconds for completion "
                   "without blocking other work. Timeout or cancellation leaves the command running. "
                   "Use KillShell to stop it.")
    parameters = {"type": "object", "properties": {
        "bash_id": {"type": "string"},
        "wait_seconds": {"type": "number",
                         "description": "Wait for completion up to this many seconds (0-60); 0 polls immediately"}}}

    def display(self, args):
        return str(arg(args, "bash_id", "id", "shell_id", default=""))

    async def run(self, ctx, args, prepared):
        shells = ctx.runtime.shells
        task_id = str(arg(args, "bash_id", "id", "shell_id", default=""))
        try:
            seconds = float(args.get("wait_seconds", 0))
        except (TypeError, ValueError) as exc:
            raise ToolError("wait_seconds must be a number between 0 and 60.") from exc
        if not math.isfinite(seconds):
            raise ToolError("wait_seconds must be a number between 0 and 60.")
        seconds = max(0.0, min(60.0, seconds))          # "wait 120" waits the longest allowed, not an error
        if not task_id:
            if seconds:
                raise ToolError("bash_id is required when waiting for a command.")
            rows = shells.list()
            text = "\n".join(f"{row['id']}  {'running' if row['running'] else 'exit ' + str(row['exit_code'])}"
                             f"  {row['command']}" for row in rows) or "No background commands."
            return ToolResult(ok=True, output=text, summary=tr(f"Задач: {len(rows)}", f"{len(rows)} tasks"))
        try:
            row = await shells.wait(task_id, seconds) if seconds else shells.read(task_id)
        except KeyError as exc:
            raise ToolError(f"No background command with id {task_id}.") from exc
        state = "running" if row["running"] else f"finished, exit code {row['exit_code']}"
        if not row["running"] and hasattr(ctx.runtime, "discard_note"):
            ctx.runtime.discard_note("shell:" + task_id)     # already delivered by this very call
        output = row["new_output"][-12000:] or "(no new output)"
        return ToolResult(ok=True, output=f"{row['command']}\nstatus: {state}\n--- new output ---\n{output}",
                          summary=state, detail=output[-3000:],
                          meta={"background_id": task_id, "running": row["running"],
                                "exit_code": row["exit_code"]})


class KillShellTool(Tool):
    name = "KillShell"
    kind = "meta"
    short = "stop a background command {shell_id}"
    description = "Stops a background command started with run_in_background (its whole process tree)."
    parameters = {"type": "object", "properties": {"shell_id": {"type": "string"}}}
    required = ("shell_id",)

    def display(self, args):
        return str(arg(args, "shell_id", "bash_id", "id", default=""))

    def permission_key(self, runtime, args):
        return "kill " + str(arg(args, "shell_id", "bash_id", "id", default=""))

    async def run(self, ctx, args, prepared):
        task_id = str(arg(args, "shell_id", "bash_id", "id", default=""))
        try:
            row = await asyncio.to_thread(ctx.runtime.shells.kill, task_id)
        except KeyError as exc:
            raise ToolError(f"No background command with id {task_id}.") from exc
        return ToolResult(ok=True, output=f"Stopped {task_id}: {row['command']}",
                          summary=tr("Остановлено", "Stopped"))


def register(registry) -> None:
    for tool in (BashTool(), BashOutputTool(), KillShellTool()):
        registry.add(tool)
