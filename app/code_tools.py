"""MemuCode: инструменты, которых не хватает обычному Work.

Work правит документы и файлы. MemuCode работает с репозиторием: запускает
команды проекта, смотрит git, собирает материал для ревью. Отдельный модуль,
потому что правила здесь строже — команда может изменить систему, а не только
файлы внутри проекта.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .work_tools import WorkError, iter_files, read_text, relative, resolve_inside

FORBIDDEN_PATTERNS = (
    r"\bformat\s+[a-z]:", r"\bmkfs\b", r"\bdiskpart\b",
    r"\bshutdown\b", r"\breboot\b", r"\bhalt\b", r"\bpoweroff\b",
    r"\breg\s+(add|delete)\b", r"\bbcdedit\b", r"\bvssadmin\b",
    r"\bnet\s+user\b", r"\bnetsh\b", r"\bsc\s+(config|delete)\b",
    r"rm\s+-rf\s+/(?:\s|$)", r"\bdel\s+/[sq]\s+[a-z]:\\", r":\(\)\s*\{.*\};:",
    r"\bchown\s+-R\s+/", r"\bchmod\s+-R\s+777\s+/",
    r"\bcurl\b[^|]*\|\s*(ba)?sh", r"\bwget\b[^|]*\|\s*(ba)?sh",
    r"\bgit\s+push\b.*--force", r"\bgit\s+reset\s+--hard\b.*origin",
)

SAFE_PREFIXES = (
    "git status", "git diff", "git log", "git branch", "git show",
    "git remote", "git rev-parse", "git ls-files", "git blame",
    "python --version", "python3 --version", "node --version", "npm --version",
    "pip --version", "cargo --version", "go version", "java -version",
    "ls", "dir", "pwd", "cat", "type", "where", "which", "echo",
)
MAX_OUTPUT = 40000
DEFAULT_TIMEOUT = 120

def _forbidden(command: str) -> Optional[str]:

    from . import code_sandbox
    return code_sandbox.forbidden_reason(command) or None

_SHELL_META = re.compile(r"[;&|<>`^\n\r]|\$\(|%[A-Za-z_]+%")

_GIT_WRITE_ARGS = re.compile(
    r"(?:^|\s)(?:-d|-D|-m|-M|-c|-C|--delete|--move|--copy|--force|-f|--set-upstream-to|"
    r"--unset-upstream|--edit-description|--output(?:=\S*)?|add|remove|rm|rename|set-url|"
    r"set-head|prune|update)(?:\s|$)")

_GIT_BRANCH_LIST = {"--list", "-l", "--contains", "--no-contains", "--merged", "--no-merged", "--points-at"}

def is_read_only(command: str) -> bool:
    raw = str(command or "").strip()
    if not raw or _SHELL_META.search(raw):
        return False
    low = " ".join(raw.lower().split())
    if not any(low == prefix or low.startswith(prefix + " ") for prefix in SAFE_PREFIXES):
        return False
    if low.startswith("git ") and _GIT_WRITE_ARGS.search(" ".join(raw.split()[2:])):
        return False
    if low.startswith("git branch "):

        words = low.split()[2:]
        listing = any(word in _GIT_BRANCH_LIST or word.split("=", 1)[0] in _GIT_BRANCH_LIST for word in words)
        if not listing and any(not word.startswith("-") for word in words):
            return False
    return True

def _safe_ref(value: Any, default: str = "") -> str:
    """Ревизия git от модели. «--output=../x» — это уже не ревизия, а опция."""
    ref = str(value or "").strip() or default
    if ref.startswith("-") or any(ch.isspace() or ord(ch) < 32 for ch in ref):
        raise WorkError("Некорректная ревизия git")
    return ref

def run_command(root: Path, command: str, *, shell: str = "", timeout: int = DEFAULT_TIMEOUT,
                cwd: str = "", session_id: str = "") -> Dict[str, Any]:
    """Запускает команду в каталоге проекта, внутри песочницы сессии."""
    from . import code_sandbox

    command = str(command or "").strip()
    if not command:
        raise WorkError("Пустая команда")
    blocked = _forbidden(command) or code_sandbox.forbidden_reason(command)
    if blocked:
        raise WorkError("Команда запрещена: она затрагивает систему целиком, "
                        "а не проект. Выполните её вручную, если это действительно нужно.")
    working = resolve_inside(root, cwd) if cwd else root
    if not working.is_dir():
        raise WorkError(f"Каталог «{cwd}» не найден")
    if sys.platform == "win32":
        executable = shell or "powershell"
        argv = (["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
                if executable.startswith("power") else ["cmd", "/c", command])
    else:
        argv = ["/bin/sh", "-c", command]
    started = time.time()
    try:
        captured = code_sandbox.launch_captured(
            session_id or "default", root, argv, cwd=str(working),
            timeout=max(1, min(600, int(timeout))),
        )
    except TimeoutError as exc:
        raise WorkError(f"Команда не завершилась за {timeout} с и была остановлена") from exc
    except OSError as exc:
        raise WorkError(f"Не удалось запустить команду: {exc}") from exc
    return {
        "command": command, "cwd": relative(root, working) or ".",
        "exit_code": captured.get("exit_code"),
        "stdout": captured.get("stdout") or "",
        "stderr": captured.get("stderr") or "",
        "ms": int((time.time() - started) * 1000),
        "read_only": is_read_only(command),
        "sandboxed": True,
    }

def _git(root: Path, *args: str, timeout: int = 30) -> str:
    if not shutil.which("git"):
        raise WorkError("git не найден в PATH")
    try:
        completed = subprocess.run(

            ["git", "-c", "core.quotepath=off", *args], cwd=str(root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkError("git не ответил вовремя") from exc
    if completed.returncode != 0 and not completed.stdout:
        raise WorkError((completed.stderr or "git вернул ошибку").strip()[:400])
    return completed.stdout

def tool_git(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    """Состояние репозитория: статус, дифф, история, ветки."""
    action = str(args.get("action") or "status").strip().lower()
    if not (root / ".git").exists():
        return {"repository": False,
                "note": "В проекте нет git-репозитория — история изменений недоступна."}
    if action == "status":

        fields = _git(root, "status", "--porcelain=v1", "-b", "-z").split("\0")
        branch = fields[0][3:] if fields and fields[0].startswith("##") else ""
        changes = []
        index = 1 if branch or (fields and fields[0].startswith("##")) else 0
        while index < len(fields):
            entry = fields[index]
            index += 1
            if len(entry) < 4:
                continue
            state, path = entry[:2], entry[3:]
            row = {"state": state.strip(), "path": path}
            if state[0] in "RC" and index < len(fields):
                row["from"] = fields[index]
                index += 1
            changes.append(row)
        return {"repository": True, "branch": branch, "changes": changes[:300],
                "clean": not changes}
    if action == "diff":
        path = str(args.get("path") or "")
        argv = ["diff", "--stat" if args.get("stat") else "--unified=3"]
        if args.get("staged"):
            argv.append("--cached")
        if path:
            argv += ["--", path]
        diff = _git(root, *argv)
        if not path and not args.get("staged") and not args.get("stat"):
            diff += _untracked_diff(root)
        return {"repository": True, "diff": diff[:MAX_OUTPUT]}
    if action == "log":
        count = max(1, min(100, int(args.get("limit") or 15)))
        raw = _git(root, "log", f"-{count}", "--pretty=format:%h\t%an\t%ar\t%s")
        rows = []
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) == 4:
                rows.append({"hash": parts[0], "author": parts[1],
                             "when": parts[2], "subject": parts[3]})
        return {"repository": True, "commits": rows}
    if action == "branches":
        return {"repository": True,
                "branches": [b.strip(" *") for b in _git(root, "branch").splitlines() if b.strip()]}
    if action == "show":
        ref = _safe_ref(args.get("ref"), "HEAD")
        return {"repository": True,
                "text": _git(root, "show", "--end-of-options", ref)[:MAX_OUTPUT]}
    raise WorkError(f"Неизвестное действие git «{action}»")

MANIFESTS = {
    "package.json": "node", "pyproject.toml": "python", "requirements.txt": "python",
    "Cargo.toml": "rust", "go.mod": "go", "pom.xml": "java",
    "build.gradle": "java", "composer.json": "php", "Gemfile": "ruby",
    "CMakeLists.txt": "cmake", "Makefile": "make", "Dockerfile": "docker",
}
TEST_HINTS = {
    "python": "pytest", "node": "npm test", "rust": "cargo test",
    "go": "go test ./...", "java": "mvn test", "php": "composer test",
    "ruby": "bundle exec rspec",
}

def tool_project_info(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    """Что это за проект: стек, манифесты, команды сборки и тестов."""
    manifests: List[str] = []
    stacks: List[str] = []
    for name, stack in MANIFESTS.items():
        if (root / name).exists():
            manifests.append(name)
            if stack not in stacks:
                stacks.append(stack)
    scripts: Dict[str, str] = {}
    package = root / "package.json"
    if package.is_file():
        try:
            import json
            data = json.loads(package.read_text(encoding="utf-8", errors="replace"))
            scripts = {str(k): str(v)[:200] for k, v in (data.get("scripts") or {}).items()}
        except Exception:
            scripts = {}
    counts: Dict[str, int] = {}
    for path in iter_files(root, limit=3000):
        suffix = path.suffix.lower()
        if suffix:
            counts[suffix] = counts.get(suffix, 0) + 1
    languages = sorted(counts.items(), key=lambda kv: -kv[1])[:8]
    return {
        "root_name": root.name,
        "stacks": stacks, "manifests": manifests,
        "npm_scripts": scripts,
        "languages": [{"suffix": s, "files": c} for s, c in languages],
        "suggested_tests": [TEST_HINTS[s] for s in stacks if s in TEST_HINTS],
        "has_git": (root / ".git").exists(),
    }

def tool_review(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    """Собирает материал для ревью: изменённые файлы и их дифф."""
    if not (root / ".git").exists():
        raise WorkError("Ревью изменений требует git-репозитория")
    base = _safe_ref(args.get("base"))
    argv = ["diff", "--unified=3"]
    if base:
        argv += ["--end-of-options", base]
    stat = _git(root, *(argv[:1] + ["--stat"] + argv[1:]))
    diff = _git(root, *argv)
    if not diff.strip() and not base:
        diff = _git(root, "diff", "--cached", "--unified=3")
        stat = _git(root, "diff", "--cached", "--stat")
    if not base:

        diff += _untracked_diff(root)
    return {"base": base or "working tree", "stat": stat[:8000],
            "diff": diff[:MAX_OUTPUT], "truncated": len(diff) > MAX_OUTPUT}

def _untracked_diff(root: Path, limit: int = 20) -> str:
    """Дифф новых (неотслеживаемых) файлов относительно пустого файла."""
    try:
        names = [name for name in _git(root, "ls-files", "-o", "--exclude-standard", "-z").split("\0") if name]
    except WorkError:
        return ""
    parts = []
    for name in names[:limit]:
        try:
            parts.append(_git(root, "diff", "--no-index", "--unified=3", "--", "/dev/null", name))
        except WorkError:
            continue
    if len(names) > limit:
        parts.append(f"# … ещё {len(names) - limit} новых файлов\n")
    return "".join(parts)

READ_TOOLS = {
    "code_git": tool_git,
    "code_project_info": tool_project_info,
    "code_review": tool_review,
}
ALL_TOOLS = dict(READ_TOOLS)

def run(name: str, root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    handler = ALL_TOOLS.get(name)
    if handler is None:
        raise WorkError(f"Неизвестный инструмент «{name}»")
    started = time.time()
    result = handler(root, args or {})
    result["tool"] = name
    result["ms"] = int((time.time() - started) * 1000)
    return result

def auto_project_root(name: str = "") -> Path:
    """Каталог для работы без выбранного проекта.

    Пользователь может просто попросить «создай файл», не заводя проект. Тогда
    он появляется в ~/MemuAI/<имя>, и туда же можно перейти из интерфейса.
    """
    base = Path.home() / "MemuAI"
    base.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w .-]+", "", str(name or "").strip())[:60].strip(" .") or "project"
    target = base / safe
    if target.exists() and not target.is_dir():
        target = base / f"{safe}-{int(time.time())}"
    target.mkdir(parents=True, exist_ok=True)
    return target.resolve()

"""Extended MemuCode tools: cached reads, deep outlines, diagnostics, and tasks.

These handlers deliberately stay dependency-free and synchronous; the API
layer decides which ones can run concurrently.
"""
from . import code_artifacts, code_context, code_sessions

DIAGNOSTIC_COMMANDS = {
    "python": ["pytest", "ruff check .", "mypy ."],
    "node": ["npm test", "npm run lint --if-present", "npm run typecheck --if-present"],
}

def tool_read_cached(root: Path, args: Dict[str, Any], session_id: str = "") -> Dict[str, Any]:
    engine = code_context.engine_for(session_id or "default", root)
    if not engine.tree:
        engine.refresh()
    return engine.read_cached(
        str(args.get("path") or ""),
        start_line=int(args.get("start_line") or 1),
        end_line=int(args.get("end_line") or 0),
        max_chars=int(args.get("max_chars") or 16000),
    )

def tool_outline_deep(root: Path, args: Dict[str, Any], session_id: str = "") -> Dict[str, Any]:
    engine = code_context.engine_for(session_id or "default", root)
    if not engine.tree:
        engine.refresh()
    return engine.outline_deep(str(args.get("path") or ""))

def tool_context(root: Path, args: Dict[str, Any], session_id: str = "") -> Dict[str, Any]:
    engine = code_context.engine_for(session_id or "default", root)
    if not engine.tree:
        engine.refresh()
    return engine.selective_context(str(args.get("query") or ""),
                                    max_files=int(args.get("max_files") or 8),
                                    max_chars=int(args.get("max_chars") or 48000))

def tool_diagnostics(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    stacks = tool_project_info(root, {}).get("stacks") or []
    explicit = str(args.get("command") or "").strip()
    commands: List[str] = []
    if explicit:
        commands = [explicit]
    else:
        for stack in stacks:
            commands.extend(DIAGNOSTIC_COMMANDS.get(str(stack), []))
        commands = commands[:4]
    if not args.get("execute"):
        return {"commands": commands,
                "note": "Pass execute:true to run them in the MemuCode sandbox. "
                        "Commands that change the project need Full access."}
    results = []
    session = str(args.get("session") or "")
    for command in commands[:3]:
        if not is_read_only(command) and not args.get("allow_write"):
            results.append({"command": command, "skipped": True,
                            "reason": "needs full access"})
            continue
        try:
            results.append(run_command(
                root, command, timeout=min(90, int(args.get("timeout") or 60)),
                session_id=session))
        except WorkError as exc:
            results.append({"command": command, "error": str(exc)})
    return {"commands": commands, "results": results}

def tool_symbols(root: Path, args: Dict[str, Any], session_id: str = "") -> Dict[str, Any]:
    engine = code_context.engine_for(session_id or "default", root)
    return engine.search_symbols(str(args.get("query") or ""),
                                 limit=int(args.get("limit") or 40))

def _skill_disabled(project_id: str, skill_id: str) -> bool:
    try:
        from .pgdata import load_preferences
        state = load_preferences().get("code_skill_state") or {}
    except Exception:
        return False
    row = state.get(project_id) if isinstance(state, dict) else None
    if not isinstance(row, dict):
        row = state.get("_") if isinstance(state, dict) else None
    disabled = row.get("disabled") if isinstance(row, dict) else []
    return str(skill_id) in {str(item) for item in disabled or []}

def tool_skill(root: Path, args: Dict[str, Any], session_id: str = "") -> Dict[str, Any]:
    from . import code_skills
    action = str(args.get("action") or "get").lower()
    if action == "list":
        return {"skills": code_skills.catalogue(root)}
    if action == "match":
        return {"skills": code_skills.match_skills(str(args.get("query") or ""))}
    skill_id = str(args.get("id") or "").strip().lower()
    if _skill_disabled(str(args.get("project_id") or ""), skill_id):
        raise WorkError("Этот скилл выключен в настройках MemuCode")
    prompt = code_skills.prompt_for(root, skill_id)
    if not prompt:
        raise WorkError(f"Скилл «{skill_id}» не найден")
    matched = code_skills.match_skills(str(args.get("query") or "")) if not skill_id else []
    return {"id": skill_id, "prompt": prompt, "suggestions": matched}

def tool_terminal(root: Path, args: Dict[str, Any], session_id: str = "") -> Dict[str, Any]:
    return code_sessions.manager.terminal(session_id or "default", root, args or {})

def tool_artifacts(root: Path, args: Dict[str, Any], session_id: str = "") -> Dict[str, Any]:
    session = session_id or "default"
    action = str(args.get("action") or "list").lower()
    if action == "list":
        return {"artifacts": code_artifacts.list_artifacts(session)}
    if action == "create":
        return code_artifacts.create(session, kind=str(args.get("kind") or "code"),
                                     title=str(args.get("title") or ""),
                                     content=str(args.get("content") or ""))
    if action == "update":
        return code_artifacts.update(session, str(args.get("id") or ""),
                                     kind=args.get("kind"),
                                     title=str(args.get("title") or "") if args.get("title") is not None else None,
                                     content=str(args.get("content") or "") if args.get("content") is not None else None)
    if action == "get":
        return code_artifacts.get(session, str(args.get("id") or ""))
    if action == "publish":
        try:
            return code_artifacts.publish(session, str(args.get("id") or ""), root,
                                          str(args.get("path") or ""))
        except (ValueError, KeyError) as exc:
            raise WorkError(str(exc)) from exc
    if action == "delete":
        code_artifacts.delete(session, str(args.get("id") or ""))
        return {"deleted": str(args.get("id") or "")}
    raise WorkError(f"Unknown artifact action: {action}")

def tool_task(root: Path, args: Dict[str, Any], session_id: str = "") -> Dict[str, Any]:
    from .code_sandbox import filtered_env
    session = session_id or "default"
    action = str(args.get("action") or "status").lower()
    if action == "list":
        return {"tasks": code_sessions.manager.list(session)}
    if action == "status":
        return code_sessions.manager.status(str(args.get("id") or ""), session)
    if action == "cancel":
        return code_sessions.manager.cancel(str(args.get("id") or ""), session)
    if action == "submit":
        command = str(args.get("command") or "").strip()
        if not command:
            raise WorkError("command is required")
        shell = str(args.get("shell") or "")
        from .code_sessions import _argv
        cwd = resolve_inside(root, str(args.get("cwd") or "")) if args.get("cwd") else root
        if not cwd.is_dir():
            raise WorkError("Каталог задачи не найден внутри проекта")
        argv = _argv(command, shell)
        return code_sessions.manager.submit(
            session, cwd, command, argv, filtered_env(session, root),
            timeout=int(args.get("timeout") or 600), label=str(args.get("label") or ""))
    raise WorkError(f"Unknown task action: {action}")

READ_TOOLS.update({
    "code_read_cached": tool_read_cached,
    "code_outline_deep": tool_outline_deep,
    "code_context": tool_context,
    "code_diagnostics": tool_diagnostics,
    "code_symbols": tool_symbols,
})
ALL_TOOLS.update({
    "code_diagnostics": tool_diagnostics,
    "code_symbols": tool_symbols,
})

PARALLEL_SAFE = {
    "code_read_cached", "code_outline_deep", "code_context", "code_symbols",
    "code_project_info", "code_review", "code_skill",
}

def parallel_safe(name: str, args: Dict[str, Any] | None = None) -> bool:
    """Read-only code tools may run together. Anything that writes stays serial."""
    args = args or {}
    action = str(args.get("action") or "").lower()
    if name == "code_diagnostics":
        return not bool(args.get("execute"))
    if name == "code_git":
        return action in ("", "status", "diff", "log", "branches", "show")
    if name == "code_artifact":
        return action in ("", "list", "get")
    if name in ("code_task", "code_terminal"):
        return action in ("", "list", "status", "read")
    return name in PARALLEL_SAFE

def run_session_tool(name: str, root: Path, args: Dict[str, Any], session_id: str = "") -> Dict[str, Any]:
    """Dispatch for session-aware tools (context cache, artifacts, tasks)."""
    started = time.time()
    handler = {
        "code_read_cached": tool_read_cached,
        "code_outline_deep": tool_outline_deep,
        "code_context": tool_context,
        "code_symbols": tool_symbols,
        "code_artifact": tool_artifacts,
        "code_task": tool_task,
        "code_skill": tool_skill,
        "code_terminal": tool_terminal,
        "code_terminal_start": lambda root, args, session: tool_terminal(root, {**args, "action": "start"}, session),
        "code_terminal_run": lambda root, args, session: tool_terminal(root, {**args, "action": "run"}, session),
        "code_terminal_stop": lambda root, args, session: tool_terminal(root, {**args, "action": "stop"}, session),
    }.get(name)
    if handler is None:
        raise WorkError(f"Unknown tool {name}")
    result = handler(root, args or {}, session_id)
    result["tool"] = name
    result["ms"] = int((time.time() - started) * 1000)
    return result
