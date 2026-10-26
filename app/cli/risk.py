"""A floor under the judge of Auto mode: the cases that always reach the person, whatever a model says.

Auto mode lets a judge — a decision model, a small chat model, or plain rules — clear routine work so the person
is asked less. A judge can be wrong, a small decision model more often than a big one, and a wrong "yes" costs far
more than a wrong "ask": a skipped question is a click, a cleared ``git push`` or an overwritten ``.env`` cannot be
taken back. So a short list of unmistakable cases is never left to the judge.

Explicit rules still rank above this list: someone who wrote ``Bash(git push:*)`` into their allow rules meant it.
Text inside quotes is data, not a command (``git commit -m "push the fix"`` is only a commit).
"""
from __future__ import annotations

import re
import shlex
from typing import List, Optional, Tuple

from .i18n import tr
from .tools.shell import split_commands

DISPOSABLE = {"node_modules", "dist", "build", "out", "target", "coverage", ".next", ".nuxt", ".svelte-kit", ".turbo",
              ".cache", ".parcel-cache", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
              ".gradle", "bin", "obj", "tmp", "temp", ".venv", "venv", ".eggs", "htmlcov", ".angular"}

_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
_Rule = Tuple["re.Pattern[str]", Tuple[str, str]]

_GIT_OPTIONS = r"(?:(?:-[cC]|--git-dir|--work-tree|--namespace)(?:=|\s+)\S+\s+|-\S+\s+)*"

_EXACT: List[_Rule] = [
    (re.compile(r"\bgit\s+" + _GIT_OPTIONS + r"push\b"), ("отправляет коммиты на удалённый сервер", "pushes commits to a remote")),
    (re.compile(r"\bgit\s+" + _GIT_OPTIONS + r"(?:reset\s+--hard|clean\s+-\w*[fx]|checkout\s+(?:--\s+)?\.(?:\s|$)|"
                r"restore\s+(?:--\S+\s+)*\.(?:\s|$)|stash\s+(?:drop|clear)|branch\s+-D\b|rebase\b|filter-branch|"
                r"reflog\s+expire|update-ref\s+-d|worktree\s+remove\s+--force)"),
     ("стирает или переписывает работу в git", "discards or rewrites git work")),
    (re.compile(r"\b(?:curl|wget)\b[^|;&]{0,400}(?:-X\s*['\"]?(?:POST|PUT|DELETE|PATCH)\b|--data\S*|--upload-file|--form\b|"
                r"\s-d\s|\s-F\s|\s-T\s|--post-data|--post-file)"),
     ("отправляет данные в интернет", "sends data to a web service")),
]

_LOOSE: List[_Rule] = [
    (re.compile(r"\b(?:npm|pnpm|yarn|bun)\s+publish\b|\bcargo\s+publish\b|\btwine\s+upload\b|\bdocker\s+push\b|"
                r"\bnuget\s+push\b|\b(?:vsce|ovsx)\s+publish\b|\bgem\s+push\b|\bgh\s+(?:release\s+(?:create|delete)|"
                r"repo\s+(?:create|delete|edit)|pr\s+merge|workflow\s+run)\b|\bterraform\s+(?:apply|destroy)\b|"
                r"\bkubectl\s+(?:apply|delete|replace)\b|\bhelm\s+(?:install|upgrade|uninstall)\b"),
     ("публикует или применяет изменения во внешней системе", "publishes or applies changes to an outside system")),
    (re.compile(r"\b(?:iwr|irm|invoke-webrequest|invoke-restmethod)\b[^|;&]{0,400}(?:-method\s+(?:post|put|delete|patch)|"
                r"-body\b|-infile\b)"),
     ("отправляет данные в интернет", "sends data to a web service")),
    (re.compile(r"\bsudo\b|\brunas\b|-verb\s+runas\b|\bsu\s+-"), ("повышает права", "runs with elevated rights")),
    (re.compile(r"\b(?:reg(?:\.exe)?\s+(?:add|delete|import|load)|setx|schtasks|sc(?:\.exe)?\s+(?:create|delete|config|stop|"
                r"start)|netsh|net\s+(?:user|localgroup|share|use)|icacls|takeown|bcdedit|diskpart|wmic|powercfg|"
                r"set-executionpolicy|set-itemproperty\s+(?:-path\s+)?['\"]?hk|\[environment\]::setenvironmentvariable|"
                r"new-service|set-service|crontab\s+-[re]|systemctl\s+(?:enable|disable|stop|mask)|launchctl)\b"),
     ("меняет системные настройки", "changes system settings")),
    (re.compile(r"\btaskkill\b[^|;&]{0,400}\s/im\b|\bpkill\b|\bkillall\b|\bstop-process\b[^|;&]{0,400}-name\b"),
     ("завершает процессы по имени", "kills processes by name")),
    (re.compile(r"\bfind\b[^|;&]{0,400}(?:-delete\b|-exec\s+rm\b)"), ("удаляет файлы пачкой", "deletes files in bulk")),
    (re.compile(r"\b(?:cat|type|more|less|head|tail|get-content|gc|cp|copy|xcopy|scp|rsync|curl|wget|base64|certutil|"
                r"select-string|sls|grep|rg)\b[^|;&]{0,400}(?:\.env(?!\.(?:example|sample|template|dist)\b)\b|[/\\]\.ssh[/\\]|"
                r"\bid_(?:rsa|dsa|ecdsa|ed25519)\b|[/\\]\.aws[/\\]|\.npmrc\b|\.pypirc\b|\.netrc\b|credentials\.json|"
                r"\.(?:pem|pfx|p12)\b|[/\\]\.gnupg[/\\])"),
     ("читает или копирует секреты", "reads or copies secrets")),
]

_WHOLE: List[_Rule] = [
    (re.compile(r"\b(?:curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod)\b[^;&]{0,600}\|\s*(?:sudo\s+)?"
                r"(?:sh|bash|zsh|dash|python3?|node|perl|ruby|pwsh|powershell|iex|invoke-expression)\b"),
     ("запускает скачанный скрипт", "runs a downloaded script")),
]

_DELETE = re.compile(r"^(?:rm|del|erase|rd|rmdir|remove-item|ri)\b", re.I)
_RECURSIVE = re.compile(r"(?:^|\s)(?:-[a-z]*r[a-z]*|--recursive|/s|-recurse)(?=\s|$)", re.I)
_WINDOWS_FLAGS = {"/s", "/q", "/f", "/a"}

_PATHS: List[_Rule] = [
    (re.compile(r"(?:^|/)\.git/"), ("внутренности репозитория git", "the git repository's own data")),
    (re.compile(r"(?:^|/)\.env(?:\.(?!example$|sample$|template$|dist$)[\w.-]+)?$"),
     ("файл с секретами .env", "a .env file with secrets")),
    (re.compile(r"(?:^|/)\.(?:ssh|aws|gnupg|kube|docker)/"), ("настройки доступа учётной записи", "the account's access settings")),
    (re.compile(r"\.(?:pem|key|pfx|p12)$|(?:^|/)id_(?:rsa|dsa|ecdsa|ed25519)"), ("ключ или сертификат", "a key or certificate")),
    (re.compile(r"(?:^|/)\.(?:npmrc|pypirc|netrc)$|(?:^|/)(?:credentials|secrets?)(?:\.(?:json|ya?ml|toml|ini|txt|cfg|conf|env))?$"),
     ("файл с учётными данными", "a file with credentials")),
    (re.compile(r"(?:^|/)\.(?:bashrc|zshrc|profile|bash_profile|zprofile)$|Microsoft\.PowerShell_profile\.ps1$"),
     ("профиль оболочки: он выполняется при каждом запуске", "a shell profile: it runs on every start")),
    (re.compile(r"(?:^|/)\.(?:memucode|claude)/settings(?:\.local)?\.json$"),
     ("настройки прав и хуков MemuCode", "MemuCode's permission and hook settings")),
    (re.compile(r"(?:^|/)\.github/workflows/|(?:^|/)\.husky/|(?:^|/)\.gitlab-ci\.yml$"),
     ("CI и хуки: выполняют код с секретами", "CI and hooks: they run code with secrets")),
]

def _disposable(target: str) -> bool:
    """A delete target that is plainly a build leftover inside the project: ``node_modules``, ``./dist/``, ``build/x``."""
    text = target.strip().strip("\"'").replace("\\", "/")
    if not text or text.startswith(("/", "~", "$", "%")) or re.match(r"^[A-Za-z]:", text) or ".." in text.split("/"):
        return False
    if "*" in text or "?" in text:
        return text.endswith((".pyc", ".pyo", ".log", ".tmp"))
    parts = [piece for piece in text.split("/") if piece and piece != "."]
    return bool(parts) and parts[0].lower() in DISPOSABLE

def _recursive_delete(part: str) -> Optional[str]:
    """Why a recursive delete is not routine, or None when it only removes build leftovers."""
    if not _DELETE.match(part.strip()) or not _RECURSIVE.search(part):
        return None
    try:
        words = shlex.split(part, posix=False)
    except ValueError:
        words = part.split()
    targets = [word for word in words[1:] if not word.startswith("-") and word.lower() not in _WINDOWS_FLAGS]
    if targets and all(_disposable(target) for target in targets):
        return None
    return tr("удаляет папки целиком", "deletes whole folders")

def risky_command(command: str) -> str:
    """Why a shell command always needs the person's word in Auto mode — "" when it is not on the list."""
    whole = " ".join(_QUOTED.sub('""', str(command or "")).split()).lower()
    for pattern, (ru, en) in _WHOLE:
        if pattern.search(whole):
            return tr(ru, en)
    for part in split_commands(command) or [command]:
        written = " ".join(_QUOTED.sub('""', part).split())
        if not written:
            continue
        reason = _recursive_delete(" ".join(part.split()))
        if reason:
            return reason
        for pattern, (ru, en) in _EXACT:
            if pattern.search(written):
                return tr(ru, en)
        lowered = written.lower()
        for pattern, (ru, en) in _LOOSE:
            if pattern.search(lowered):
                return tr(ru, en)
    return ""

def risky_path(path: str) -> str:
    """Why a change to this file always needs the person's word in Auto mode — "" when it is not on the list."""
    target = str(path or "").replace("\\", "/")
    for pattern, (ru, en) in _PATHS:
        if pattern.search(target):
            return tr(ru, en)
    return ""

def risky_reason(tool_name: str, kind: str, key: str) -> str:
    """The reason (in the current language) a call is never cleared by the judge, or "" when the judge may decide."""
    if tool_name == "Bash":
        return risky_command(key)
    if kind == "edit":
        return risky_path(key)
    return ""
