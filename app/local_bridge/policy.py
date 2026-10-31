"""Bridge policy: what the server-side AI may ask the user's computer to do.

Default-deny, always.  A request is allowed only when ALL of these hold:

* the bridge is enabled and not revoked;
* its action class (read / write / execute / browser) is switched on;
* every path it touches resolves inside an explicitly allowed root;
* an ``execute`` request names an allowlisted command with allowlisted
  arguments — argv lists only, never ``shell=True``, never shell
  metacharacters, never argument substitution;
* a ``browser`` request names an http(s) URL outside private networks
  (the host browser bridge performs it; this module never opens it).

Confirmation is a separate layer (see :mod:`app.local_bridge.agent`):
``"always"`` asks on every request, ``"once"`` remembers one approval per
(action class, scope) for the process lifetime.
"""
from __future__ import annotations

import ipaddress
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence

ACTION_CLASSES = ("read", "write", "execute", "browser")

# command -> allowed first arguments (subcommands / flags).  Anything else
# is denied.  Scripts are additionally required to live inside an allowed
# root (checked by the agent, which knows the roots).
ALLOWED_COMMANDS: Dict[str, FrozenSet[str]] = {
    "git": frozenset({"status", "log", "diff", "show", "branch", "rev-parse"}),
    "python": frozenset({"-m", "-c", "-V", "--version"}),
    "python3": frozenset({"-m", "-c", "-V", "--version"}),
    "pytest": frozenset({"-q", "-v", "-k", "--collect-only", "-x"}),
    "node": frozenset({"--version", "-v", "-e"}),
    "npm": frozenset({"--version", "test", "run"}),
    "cargo": frozenset({"test", "check", "build", "--version"}),
    "go": frozenset({"test", "build", "vet", "version"}),
}

# Modules a ``python -m`` invocation may run: the test runner only.
ALLOWED_PY_MODULES = frozenset({"pytest"})
_SHELL_META_RE = re.compile(r"[;|&$`\\\n\r<>]")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


@dataclass
class BridgePolicy:
    roots: Sequence[str] = ()
    enabled_classes: FrozenSet[str] = frozenset({"read"})
    confirm_mode: str = "always"  # "always" | "once"
    enabled: bool = False

    def root_paths(self) -> List[Path]:
        out = []
        for root in self.roots:
            try:
                path = Path(root).expanduser()
            except (TypeError, ValueError):
                continue
            if path.is_absolute():
                out.append(path.resolve())
        return out

    def contains(self, path: str | Path) -> bool:
        """True when ``path`` resolves inside one of the allowed roots."""
        try:
            target = Path(str(path)).expanduser().resolve()
        except (TypeError, ValueError, OSError):
            return False
        for root in self.root_paths():
            if target == root or root in target.parents:
                return True
        return False

    def class_allowed(self, action_class: str) -> bool:
        return action_class in ACTION_CLASSES and action_class in self.enabled_classes

    def check_argv(self, argv: Sequence[str]) -> Optional[str]:
        """Return an error string, or None when the argv is allowlisted."""
        if not argv:
            return "empty command"
        argv = [str(a) for a in argv]
        command = Path(argv[0]).name
        allowed = ALLOWED_COMMANDS.get(command)
        if allowed is None:
            return f"command not allowlisted: {command}"
        for arg in argv:
            if _SHELL_META_RE.search(arg):
                return "shell metacharacters are not allowed"
        rest = argv[1:]
        if command in ("python", "python3"):
            if not rest:
                return "python needs -m or a script inside an allowed root"
            if rest[0] == "-m":
                if len(rest) < 2 or rest[1] not in ALLOWED_PY_MODULES:
                    return "python -m module is not allowlisted"
                return None
            if rest[0] in ("-V", "--version"):
                return None
            if rest[0] == "-c":
                return "python -c is not allowlisted"
            # a script path: must be inside an allowed root
            if not self.contains(rest[0]):
                return "script is outside the allowed roots"
            return None
        if command == "node":
            if rest and rest[0] in ("--version", "-v"):
                return None
            if rest and rest[0] == "-e":
                return "node -e is not allowlisted"
            if rest and self.contains(rest[0]):
                return None
            return "node script is outside the allowed roots"
        if command in ("git", "pytest", "npm", "cargo", "go"):
            if not rest:
                return None if command == "git" else f"{command} needs an argument"
            if command == "git" and rest[0] not in allowed:
                return f"git subcommand is not allowlisted: {rest[0]}"
            if command == "pytest":
                # pytest args: flags from the allowlist, or paths inside roots
                for arg in rest:
                    if arg.startswith("-"):
                        if arg.split("=")[0] not in allowed:
                            return f"pytest flag is not allowlisted: {arg}"
                    elif not self.contains(arg):
                        return "pytest path is outside the allowed roots"
                return None
            if command == "npm" and rest[0] not in allowed:
                return f"npm subcommand is not allowlisted: {rest[0]}"
            if command in ("cargo", "go") and rest[0] not in allowed:
                return f"{command} subcommand is not allowlisted: {rest[0]}"
            return None
        return f"command not allowlisted: {command}"

    def check_url(self, url: str) -> Optional[str]:
        """Browser URLs: public http(s) only, no private/LAN hosts."""
        raw = str(url or "").strip()
        if not _URL_RE.match(raw):
            return "only http(s) URLs are allowed"
        try:
            parsed = urllib.parse.urlparse(raw)
            host = (parsed.hostname or "").lower()
            if not host:
                return "URL has no host"
            if host in ("localhost",) or host.endswith(".local"):
                return "private hosts are not allowed"
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return "private IP ranges are not allowed"
        except ValueError:
            # Not an IP literal: a public DNS name.  DNS rebinding is out of
            # scope for this layer; the host browser bridge re-checks.
            return None
        return None
