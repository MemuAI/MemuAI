"""MemuNote (Round 33, item #9): run the open file — ``.py``, ``.js``, ``.ps1``, ``.bat``, ``.sh`` and more.

Routes (bound onto ``server.Handler`` by :func:`register`, one call at the end of ``server.py``):

* ``GET  /api/run/info?path=``        what would run: interpreter found or not, the command line, a trust flag;
* ``POST /api/run/start``             ``{path, args?, confirmed?, remember?}`` → ``{id, cmd}``;
* ``GET  /api/run/poll?id=&since=``   long-poll (≤ 20 s): ``{chunks: [{seq, s: "out"|"err"|"sys", t}], done, code}``;
* ``POST /api/run/input``             ``{id, text}`` → stdin (the page adds the newline);
* ``POST /api/run/eof``               ``{id}`` closes stdin (Ctrl+D);
* ``POST /api/run/stop``              ``{id}`` kills the whole process tree;
* ``GET/POST /api/run/config``        per-extension interpreter override (``memunote/run.json``).

Safety:
* only a file the page has OPENED (``NoteApp.allowed``) can be run — the API cannot start an arbitrary path;
* no shell, ever: the command is an argv list (``subprocess.Popen(list)``). Arguments are split by
  :func:`split_args`; a ``.bat``/``.cmd`` (which ``cmd.exe`` re-parses) refuses paths and arguments containing
  ``& | < > ^ % !`` so a file name can never smuggle a second command;
* the first run of a file needs ``confirmed: true`` (the page shows a confirmation); "Always allow" stores the
  file in the trusted list. A file downloaded from the internet (Windows ``Zone.Identifier`` ZoneId ≥ 3) is
  flagged and never auto-trusted;
* the working directory is the file's folder, output is capped (last 2 MB kept), at most 6 runs at a time,
  every child is killed when MemuNote exits.
"""
from __future__ import annotations

import atexit
import codecs
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .paths import note_dir, read_json, write_json

IS_WINDOWS = os.name == "nt"
MAX_OUTPUT = 2 * 1024 * 1024
MAX_RUNS = 6
POLL_TIMEOUT = 20.0
BATCH_UNSAFE = re.compile(r"[&|<>^%!\r\n\"]")

LANGS: Dict[str, Tuple[str, List[str], str]] = {
    ".py": ("Python", ["py", "python", "python3"] if IS_WINDOWS else ["python3", "python"], "python"),
    ".pyw": ("Python", ["pyw", "pythonw", "py", "python"] if IS_WINDOWS else ["python3"], "python"),
    ".js": ("Node.js", ["node"], "plain"),
    ".mjs": ("Node.js", ["node"], "plain"),
    ".cjs": ("Node.js", ["node"], "plain"),
    ".ts": ("TypeScript", ["tsx", "deno", "bun", "ts-node"], "ts"),
    ".mts": ("TypeScript", ["tsx", "deno", "bun"], "ts"),
    ".ps1": ("PowerShell", ["pwsh", "powershell"] if IS_WINDOWS else ["pwsh"], "powershell"),
    ".bat": ("Batch", ["cmd"] if IS_WINDOWS else [], "batch"),
    ".cmd": ("Batch", ["cmd"] if IS_WINDOWS else [], "batch"),
    ".sh": ("Shell", ["bash", "sh"], "plain"),
    ".bash": ("Bash", ["bash"], "plain"),
    ".zsh": ("Zsh", ["zsh"], "plain"),
    ".rb": ("Ruby", ["ruby"], "plain"),
    ".php": ("PHP", ["php"], "plain"),
    ".pl": ("Perl", ["perl"], "plain"),
    ".lua": ("Lua", ["lua", "luajit"], "plain"),
    ".r": ("R", ["Rscript"], "plain"),
    ".go": ("Go", ["go"], "go"),
    ".java": ("Java", ["java"], "plain"),
    ".kts": ("Kotlin script", ["kotlin", "kotlinc"], "kotlin"),
    ".dart": ("Dart", ["dart"], "plain"),
    ".jl": ("Julia", ["julia"], "plain"),
    ".swift": ("Swift", ["swift"], "plain"),
    ".groovy": ("Groovy", ["groovy"], "plain"),
    ".vbs": ("VBScript", ["cscript"] if IS_WINDOWS else [], "vbs"),
    ".ahk": ("AutoHotkey", ["AutoHotkey64", "AutoHotkey", "AutoHotkeyU64"] if IS_WINDOWS else [], "plain"),
}
INSTALL_HINTS = {
    "Python": "https://www.python.org/downloads/", "Node.js": "https://nodejs.org/", "TypeScript": "https://nodejs.org/ + npm i -g tsx",
    "PowerShell": "https://aka.ms/powershell", "Ruby": "https://www.ruby-lang.org/", "PHP": "https://www.php.net/",
    "Go": "https://go.dev/dl/", "Java": "https://adoptium.net/", "Lua": "https://www.lua.org/", "R": "https://cran.r-project.org/",
    "Shell": "https://git-scm.com/ (Git Bash)", "Bash": "https://git-scm.com/ (Git Bash)", "Perl": "https://strawberryperl.com/",
    "Dart": "https://dart.dev/", "Julia": "https://julialang.org/", "Kotlin script": "https://kotlinlang.org/",
}

_RUNS: Dict[str, "Run"] = {}
_LOCK = threading.Lock()

def _config_path():
    return note_dir() / "run.json"

def load_config() -> Dict[str, Any]:
    raw = read_json(_config_path(), {})
    interp = raw.get("interpreters") if isinstance(raw.get("interpreters"), dict) else {}
    trusted = raw.get("trusted") if isinstance(raw.get("trusted"), list) else []
    args = raw.get("args") if isinstance(raw.get("args"), dict) else {}
    return {
        "interpreters": {k: str(v)[:1024] for k, v in interp.items() if isinstance(k, str) and k in LANGS and isinstance(v, str) and v.strip()},
        "trusted": [str(p)[:4096] for p in trusted if isinstance(p, str)][-500:],
        "args": {str(k)[:4096]: str(v)[:2000] for k, v in list(args.items())[-300:] if isinstance(v, str)},
        "save_before_run": raw.get("save_before_run", True) is not False,
    }

def save_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    write_json(_config_path(), cfg)
    return load_config()

def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))

def is_trusted(path: str) -> bool:
    return _key(path) in {_key(p) for p in load_config()["trusted"]}

def trust(path: str) -> None:
    cfg = load_config()
    if not any(_key(p) == _key(path) for p in cfg["trusted"]):
        cfg["trusted"].append(os.path.abspath(path))
        save_config(cfg)

def from_internet(path: str) -> bool:
    """Windows Mark of the Web: the ``Zone.Identifier`` stream of a downloaded file says ZoneId=3 (Internet)."""
    if not IS_WINDOWS:
        return False
    try:
        with open(path + ":Zone.Identifier", encoding="utf-8", errors="replace") as handle:
            text = handle.read(4096)
    except OSError:
        return False
    m = re.search(r"ZoneId\s*=\s*(\d+)", text)
    return bool(m and int(m.group(1)) >= 3)

def _which(name: str) -> str:
    if os.path.isabs(name):
        return name if os.path.isfile(name) else ""
    found = shutil.which(name) or ""

    if found and IS_WINDOWS and "windowsapps" in found.lower() and name.lower().startswith("python"):
        return ""
    return found

def resolve(path: str, override: str = "") -> Dict[str, Any]:
    """What running *path* means: ``{runnable, lang, exe, argv_prefix, missing, hint}``."""
    ext = os.path.splitext(path)[1].lower()
    lang = LANGS.get(ext)
    if not lang:
        return {"runnable": False, "lang": "", "ext": ext}
    label, candidates, style = lang
    custom = override or load_config()["interpreters"].get(ext, "")
    exe = ""
    if custom:
        parts = split_args(custom)
        exe = _which(parts[0]) if parts else ""
        if exe:
            return {"runnable": True, "lang": label, "ext": ext, "exe": exe, "extra": parts[1:], "style": style, "custom": custom}
    for name in candidates:
        exe = _which(name)
        if exe:
            break
    if not exe:
        return {"runnable": True, "lang": label, "ext": ext, "exe": "", "missing": candidates[0] if candidates else "",
                "hint": INSTALL_HINTS.get(label, ""), "style": style, "custom": custom}
    return {"runnable": True, "lang": label, "ext": ext, "exe": exe, "extra": [], "style": style, "custom": custom}

def build_argv(path: str, info: Dict[str, Any], args: List[str]) -> List[str]:
    exe, extra, style = info["exe"], list(info.get("extra") or []), info.get("style")
    name = os.path.basename(exe).lower()
    if style == "python":
        argv = [exe] + extra
        if name in ("py", "py.exe") and not extra:
            argv.append("-3")
        return argv + ["-u", path] + args
    if style == "powershell":
        return [exe] + extra + ["-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path] + args
    if style == "batch":

        for item in [path] + args:
            if BATCH_UNSAFE.search(item):
                raise ValueError("batch_unsafe")
        return [exe, "/d", "/c", "call", path] + args
    if style == "vbs":
        return [exe, "//nologo", path] + args
    if style == "go":
        return [exe] + extra + ["run", path] + args
    if style == "kotlin":
        return [exe] + extra + ["-script", path] + args
    if style == "ts":
        if name.startswith("deno"):
            return [exe, "run", "-A", path] + args
        if name.startswith("bun"):
            return [exe, "run", path] + args
        return [exe] + extra + [path] + args
    return [exe] + extra + [path] + args

def split_args(text: str) -> List[str]:
    """Split a user-typed argument string like a shell would, but never run a shell."""
    text = str(text or "").strip()
    if not text:
        return []
    try:

        parts = shlex.split(text, posix=not IS_WINDOWS)
    except ValueError:
        parts = text.split()
    if IS_WINDOWS:
        parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]
    return [p for p in parts if p != ""][:200]

def display_cmd(argv: List[str]) -> str:
    out = []
    for i, item in enumerate(argv):
        shown = os.path.basename(item) if i == 0 else item
        out.append(f'"{shown}"' if (" " in shown or not shown) else shown)
    return " ".join(out)

def _oem_codec() -> str:
    if not IS_WINDOWS:
        return "utf-8"
    try:
        import ctypes
        return "cp%d" % ctypes.windll.kernel32.GetOEMCP()
    except Exception:  # noqa: BLE001
        return "cp866"

class _Decoder:
    """UTF-8 first; if a stream turns out not to be UTF-8 (cmd.exe and old console tools write the OEM code
    page, cp866 on Russian Windows), switch that stream to the OEM code page for good."""

    def __init__(self) -> None:
        self.dec = codecs.getincrementaldecoder("utf-8")("strict")
        self.fallback = False

    def feed(self, data: bytes, final: bool = False) -> str:
        if not self.fallback:
            try:
                return self.dec.decode(data, final)
            except UnicodeDecodeError:
                self.fallback = True
                pending = self.dec.getstate()[0]
                self.dec = codecs.getincrementaldecoder(_oem_codec())("replace")
                data = pending + data
        return self.dec.decode(data, final)

class Run:
    def __init__(self, path: str, argv: List[str], env: Dict[str, str]) -> None:
        self.id = uuid.uuid4().hex[:16]
        self.path = path
        self.argv = argv
        self.cmd = display_cmd(argv)
        self.chunks: List[Tuple[int, str, str]] = []
        self.seq = 0
        self.size = 0
        self.dropped = 0
        self.code: Optional[int] = None
        self.done = False
        self.started = time.time()
        self.ended = 0.0
        self.cond = threading.Condition()
        flags = 0
        kwargs: Dict[str, Any] = {}
        if IS_WINDOWS:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
        else:
            kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(argv, cwd=os.path.dirname(path) or None, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags, **kwargs)
        self.readers = [threading.Thread(target=self._read, args=(self.proc.stdout, "out"), daemon=True),
                        threading.Thread(target=self._read, args=(self.proc.stderr, "err"), daemon=True)]
        for th in self.readers:
            th.start()
        threading.Thread(target=self._wait, daemon=True).start()

    def push(self, stream: str, text: str) -> None:
        if not text:
            return
        with self.cond:
            self.seq += 1
            self.chunks.append((self.seq, stream, text))
            self.size += len(text)
            while self.size > MAX_OUTPUT and len(self.chunks) > 1:
                _, _, old = self.chunks.pop(0)
                self.size -= len(old)
                self.dropped += len(old)
            self.cond.notify_all()

    def _read(self, pipe, stream: str) -> None:
        dec = _Decoder()
        try:
            while True:
                data = pipe.read1(65536) if hasattr(pipe, "read1") else pipe.read(4096)
                if not data:
                    break
                self.push(stream, dec.feed(data).replace("\r\n", "\n"))
            self.push(stream, dec.feed(b"", True))
        except (OSError, ValueError):
            pass

    def _wait(self) -> None:
        code = self.proc.wait()
        for th in self.readers:
            th.join(timeout=5)
        with self.cond:
            self.code = code
            self.done = True
            self.ended = time.time()
            self.cond.notify_all()

    def poll(self, since: int, timeout: float) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                fresh = [c for c in self.chunks if c[0] > since]
                if fresh or self.done or time.monotonic() >= deadline:
                    break
                self.cond.wait(max(0.05, deadline - time.monotonic()))

        if fresh and not self.done:
            time.sleep(0.03)
            with self.cond:
                fresh = [c for c in self.chunks if c[0] > since]
        with self.cond:
            done = self.done and not [c for c in self.chunks if c[0] > (fresh[-1][0] if fresh else since)]
            return {"ok": True, "id": self.id, "chunks": [{"seq": s, "s": k, "t": t} for s, k, t in fresh],
                    "seq": fresh[-1][0] if fresh else since, "done": done, "code": self.code if done else None,
                    "dropped": self.dropped, "elapsed": round((self.ended or time.time()) - self.started, 2)}

    def write(self, text: str) -> None:
        if self.done or not self.proc.stdin:
            raise ValueError("not_running")
        try:
            self.proc.stdin.write(text.encode("utf-8"))
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise ValueError("stdin_closed") from exc

    def eof(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass

    def stop(self) -> None:
        if self.done:
            return
        pid = self.proc.pid
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000), timeout=10)
            else:
                try:
                    os.killpg(pid, signal.SIGTERM)
                except OSError:
                    self.proc.terminate()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except OSError:
                        self.proc.kill()
        except Exception:  # noqa: BLE001
            try:
                self.proc.kill()
            except OSError:
                pass

def child_env(info: Dict[str, Any]) -> Dict[str, str]:
    env = dict(os.environ)
    for k in ("MEMUNOTE_TOKEN",):
        env.pop(k, None)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.setdefault("FORCE_COLOR", "1")
    env.setdefault("TERM", "xterm-256color")
    if getattr(sys, "frozen", False):

        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if os.path.normcase(p) != os.path.normcase(meipass))
        for k in ("TCL_LIBRARY", "TK_LIBRARY", "PYTHONHOME", "PYTHONPATH", "_MEIPASS2"):
            env.pop(k, None)
    return env

def start(path: str, args: List[str]) -> Run:
    info = resolve(path)
    if not info.get("runnable"):
        raise ValueError("not_runnable")
    if not info.get("exe"):
        raise ValueError("no_interpreter")
    argv = build_argv(path, info, args)
    with _LOCK:
        alive = [r for r in _RUNS.values() if not r.done]
        if len(alive) >= MAX_RUNS:
            raise ValueError("too_many_runs")
        for rid in [rid for rid, r in _RUNS.items() if r.done and time.time() - r.ended > 600]:
            _RUNS.pop(rid, None)
        run = Run(path, argv, child_env(info))
        _RUNS[run.id] = run
    return run

def get(run_id: str) -> Run:
    run = _RUNS.get(str(run_id or ""))
    if not run:
        raise FileNotFoundError("run")
    return run

@atexit.register
def stop_all() -> None:
    for run in list(_RUNS.values()):
        try:
            run.stop()
        except Exception:  # noqa: BLE001
            pass

def _path_from(handler, value: Any) -> str:
    from .server import _clean_path
    path = _clean_path(value)
    if not handler.app.allowed(path):
        raise PermissionError("only an open file can be run")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    return path

def api_run_info(self, method: str) -> None:
    path = _path_from(self, self._query().get("path"))
    info = resolve(path)
    cfg = load_config()
    out = {"ok": True, "runnable": bool(info.get("runnable")), "lang": info.get("lang", ""), "ext": info.get("ext", ""),
           "found": bool(info.get("exe")), "interpreter": info.get("exe", ""), "missing": info.get("missing", ""),
           "hint": info.get("hint", ""), "custom": info.get("custom", ""), "trusted": is_trusted(path),
           "internet": from_internet(path), "args": cfg["args"].get(_key(path), ""), "platform": os.name}
    if info.get("exe"):
        try:
            out["cmd"] = display_cmd(build_argv(path, info, split_args(out["args"])))
        except ValueError as exc:
            out["cmd"], out["error"] = "", str(exc)
    self._json(out)

def api_run_start(self, method: str) -> None:
    body = self._json_body()
    path = _path_from(self, body.get("path"))
    args_text = str(body.get("args") or "")[:2000]
    internet = from_internet(path)
    if not is_trusted(path) or internet:
        if not body.get("confirmed"):
            self._error("confirm_required", 409, internet=internet)
            return
        if body.get("remember") and not internet:
            trust(path)
    cfg = load_config()
    if cfg["args"].get(_key(path), "") != args_text:
        cfg["args"][_key(path)] = args_text
        if not args_text:
            cfg["args"].pop(_key(path), None)
        save_config(cfg)
    try:
        run = start(path, split_args(args_text))
    except ValueError as exc:
        self._error(str(exc), 400)
        return
    except OSError as exc:
        self._error("start_failed", 500, detail=str(exc)[:300])
        return
    self._json({"ok": True, "id": run.id, "cmd": run.cmd, "cwd": os.path.dirname(path)})

def api_run_poll(self, method: str) -> None:
    q = self._query()
    run = get(q.get("id"))
    try:
        since = int(q.get("since") or 0)
    except ValueError:
        since = 0
    self._json(run.poll(since, POLL_TIMEOUT))

def api_run_input(self, method: str) -> None:
    body = self._json_body()
    get(body.get("id")).write(str(body.get("text") or "")[:1_000_000])
    self._json({"ok": True})

def api_run_eof(self, method: str) -> None:
    get(self._json_body().get("id")).eof()
    self._json({"ok": True})

def api_run_stop(self, method: str) -> None:
    get(self._json_body().get("id")).stop()
    self._json({"ok": True})

def api_run_config(self, method: str) -> None:
    cfg = load_config()
    if method == "POST":
        body = self._json_body()
        ext = str(body.get("ext") or "").lower()
        if "interpreter" in body:
            if ext not in LANGS:
                self._error("unknown extension")
                return
            value = str(body.get("interpreter") or "").strip()[:1024]
            if value:
                parts = split_args(value)
                if not parts or not _which(parts[0]):
                    self._error("interpreter_not_found", 400)
                    return
                cfg["interpreters"][ext] = value
            else:
                cfg["interpreters"].pop(ext, None)
        if "untrust" in body:
            key = _key(str(body.get("untrust") or ""))
            cfg["trusted"] = [p for p in cfg["trusted"] if _key(p) != key]
        if "save_before_run" in body:
            cfg["save_before_run"] = bool(body.get("save_before_run"))
        cfg = save_config(cfg)
    self._json({"ok": True, "interpreters": cfg["interpreters"], "trusted": cfg["trusted"],
                "save_before_run": cfg["save_before_run"], "languages": {ext: v[0] for ext, v in LANGS.items()}})

def register(handler_cls) -> None:
    """Attach the run routes to the MemuNote request handler class."""
    for name in ("info", "start", "poll", "input", "eof", "stop", "config"):
        setattr(handler_cls, "api_run_" + name, globals()["api_run_" + name])
