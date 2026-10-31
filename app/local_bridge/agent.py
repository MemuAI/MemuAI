"""The local bridge agent: executes server-AI requests on the user's PC.

One process-wide :class:`BridgeService`.  Every execution goes through the
same gate, in order:

1. revoked? -> denied immediately, nothing runs;
2. bridge enabled in settings? -> otherwise denied;
3. action class switched on? -> otherwise denied;
4. policy check (roots / allowlist / URL) -> otherwise denied;
5. confirmation (``always`` / ``once``) -> pending until the user
   approves, or denied;
6. execution with a runner (default: ``subprocess`` without a shell, or
   plain file IO capped at 1 MiB), journaled.

The journal is append-only JSON lines and never contains token-looking
values: anything shaped like a credential is redacted before writing.
``revoke()`` is instant: it sets the flag, drops every pending request
and journal-records the revocation.  A revoked bridge stays revoked until
the process restarts with the bridge re-enabled in settings.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .policy import ACTION_CLASSES, BridgePolicy

MAX_IO_BYTES = 1024 * 1024
EXEC_TIMEOUT_S = 60.0

_SECRET_RE = re.compile(
    r"(?i)(token|secret|password|api[_-]?key|bearer)[\"'=: ]+[^\"'\s,}]{4,}")
def redact(text: str) -> str:
    """Redact credential-shaped substrings from journal/log text."""
    text = _SECRET_RE.sub(r"\1=••••", str(text or ""))
    return text


@dataclass
class BridgeRequest:
    action_class: str
    kind: str  # "read" | "write" | "execute" | "browser"
    target: str = ""  # file path, or URL for browser
    argv: List[str] = field(default_factory=list)
    data: bytes = b""
    id: str = field(default_factory=lambda: "br_" + uuid.uuid4().hex[:12])
    origin: str = "server"


@dataclass
class BridgeResult:
    status: str  # "ok" | "denied" | "pending" | "error" | "needs_host"
    output: str = ""
    error: str = ""


def request_from_dict(payload: Dict[str, Any]) -> BridgeRequest:
    data = payload.get("data", "")
    raw = b""
    if isinstance(data, str):
        raw = data.encode("utf-8", "replace")
    elif isinstance(data, (bytes, bytearray)):
        raw = bytes(data)
    argv = payload.get("argv") or []
    return BridgeRequest(
        action_class=str(payload.get("action_class") or payload.get("kind") or ""),
        kind=str(payload.get("kind") or ""),
        target=str(payload.get("target") or payload.get("path") or payload.get("url") or ""),
        argv=[str(a) for a in argv] if isinstance(argv, list) else [],
        data=raw,
        id=str(payload.get("id") or ("br_" + uuid.uuid4().hex[:12])),
        origin=str(payload.get("origin") or "server"),
    )


def result_to_dict(result: BridgeResult) -> Dict[str, Any]:
    return {"status": result.status, "output": result.output, "error": result.error}


class BridgeService:
    def __init__(self, policy: Optional[BridgePolicy] = None, *,
                 journal_path: Optional[str | Path] = None,
                 runner: Optional[Callable[..., Any]] = None) -> None:
        self.policy = policy or BridgePolicy()
        self.journal_path = Path(journal_path) if journal_path else None
        self._runner = runner
        self._revoked = False
        self._pending: Dict[str, BridgeRequest] = {}
        self._approved_once: set = set()
        self._journal: List[Dict[str, Any]] = []

    # ---- state ---------------------------------------------------------
    @property
    def revoked(self) -> bool:
        return self._revoked

    def revoke(self) -> None:
        """Instant one-click revocation: nothing else runs afterwards."""
        self._revoked = True
        dropped = len(self._pending)
        self._pending.clear()
        self._approved_once.clear()
        self._write_journal({"event": "revoked", "dropped_pending": dropped})

    # ---- journal -------------------------------------------------------
    def _write_journal(self, entry: Dict[str, Any]) -> None:
        entry = dict(entry)
        entry["ts"] = time.time()
        for key in ("target", "output", "error", "argv"):
            if key in entry and entry[key] is not None:
                value = entry[key]
                if isinstance(value, list):
                    value = " ".join(str(v) for v in value)
                entry[key] = redact(str(value))
        self._journal.append(entry)
        if self.journal_path is not None:
            try:
                self.journal_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.journal_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                pass

    def journal(self, limit: int = 100) -> List[Dict[str, Any]]:
        return list(self._journal[-limit:])

    def pending(self) -> List[BridgeRequest]:
        return list(self._pending.values())

    # ---- confirmation --------------------------------------------------
    def _confirm_scope(self, request: BridgeRequest) -> str:
        if request.action_class == "execute":
            return request.argv[0] if request.argv else ""
        return request.target

    def _needs_confirmation(self, request: BridgeRequest) -> bool:
        if self.policy.confirm_mode == "once":
            return (request.action_class, self._confirm_scope(request)) not in self._approved_once
        return True  # "always"

    def confirm_pending(self, req_id: str, approve: bool) -> Optional[BridgeResult]:
        """Resolve a pending request. Approve -> execute now; deny -> denied."""
        request = self._pending.pop(req_id, None)
        if request is None:
            return None
        if not approve:
            result = BridgeResult("denied", error="denied by user")
            self._write_journal({"event": "denied", "id": req_id,
                                 "action_class": request.action_class,
                                 "kind": request.kind, "target": request.target})
            return result
        if self.policy.confirm_mode == "once":
            self._approved_once.add((request.action_class, self._confirm_scope(request)))
        return self._run(request)

    # ---- gate + execution ----------------------------------------------
    def execute(self, request: BridgeRequest,
                confirm_callback: Optional[Callable[[BridgeRequest], bool]] = None) -> BridgeResult:
        if self._revoked:
            return self._deny(request, "bridge revoked")
        if not self.policy.enabled:
            return self._deny(request, "bridge is disabled")
        if request.action_class not in ACTION_CLASSES or request.kind not in ACTION_CLASSES:
            return self._deny(request, "unknown action class")
        if not self.policy.class_allowed(request.action_class):
            return self._deny(request, f"action class is switched off: {request.action_class}")
        denied = self._policy_check(request)
        if denied:
            return self._deny(request, denied)
        if self._needs_confirmation(request):
            if confirm_callback is not None:
                if not confirm_callback(request):
                    return self._deny(request, "denied by user")
                if self.policy.confirm_mode == "once":
                    self._approved_once.add((request.action_class, self._confirm_scope(request)))
            else:
                self._pending[request.id] = request
                self._write_journal({"event": "pending", "id": request.id,
                                     "action_class": request.action_class,
                                     "kind": request.kind, "target": request.target})
                return BridgeResult("pending", error="waiting for user confirmation")
        return self._run(request)

    def _deny(self, request: BridgeRequest, reason: str) -> BridgeResult:
        self._write_journal({"event": "denied", "id": request.id,
                             "action_class": request.action_class,
                             "kind": request.kind, "target": request.target,
                             "error": reason})
        return BridgeResult("denied", error=reason)

    def _policy_check(self, request: BridgeRequest) -> Optional[str]:
        if request.kind == "read":
            if not self.policy.contains(request.target):
                return "path is outside the allowed roots"
        elif request.kind == "write":
            if not self.policy.contains(request.target):
                return "path is outside the allowed roots"
            if len(request.data) > MAX_IO_BYTES:
                return "write is too large"
        elif request.kind == "execute":
            error = self.policy.check_argv(request.argv)
            if error:
                return error
            cwd = self._exec_cwd(request)
            if cwd is not None and not self.policy.contains(str(cwd)):
                return "working directory is outside the allowed roots"
        elif request.kind == "browser":
            error = self.policy.check_url(request.target)
            if error:
                return error
        else:
            return "unknown request kind"
        return None

    def _exec_cwd(self, request: BridgeRequest) -> Optional[Path]:
        # Scripts run with cwd = the script's folder when it is inside a root;
        # other commands run in the first allowed root.
        if request.argv:
            command = Path(str(request.argv[0])).name
            if command in ("python", "python3", "node") and len(request.argv) > 1 \
                    and not str(request.argv[1]).startswith("-"):
                try:
                    return Path(str(request.argv[1])).expanduser().resolve().parent
                except (TypeError, ValueError, OSError):
                    return None
        roots = self.policy.root_paths()
        return roots[0] if roots else None

    def _run(self, request: BridgeRequest) -> BridgeResult:
        started = time.monotonic()
        try:
            if request.kind == "read":
                path = Path(request.target).expanduser().resolve()
                data = path.read_bytes()[:MAX_IO_BYTES]
                result = BridgeResult("ok", output=data.decode("utf-8", "replace"))
            elif request.kind == "write":
                path = Path(request.target).expanduser().resolve()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(request.data)
                result = BridgeResult("ok", output=f"wrote {len(request.data)} bytes")
            elif request.kind == "execute":
                result = self._run_command(request)
            elif request.kind == "browser":
                # The host browser bridge performs the open; this layer only
                # authorises it.  Never open anything from here.
                result = BridgeResult("needs_host",
                                      output=f"browser action authorised: {request.target}")
            else:
                result = BridgeResult("error", error="unknown request kind")
        except OSError as exc:
            result = BridgeResult("error", error=f"{type(exc).__name__}: {exc}")
        duration_ms = round((time.monotonic() - started) * 1000)
        self._write_journal({"event": "executed", "id": request.id,
                             "action_class": request.action_class,
                             "kind": request.kind, "target": request.target,
                             "argv": request.argv, "status": result.status,
                             "error": result.error, "duration_ms": duration_ms})
        return result

    def _run_command(self, request: BridgeRequest) -> BridgeResult:
        argv = [str(a) for a in request.argv]
        roots = self.policy.root_paths()
        cwd = self._exec_cwd(request) or (roots[0] if roots else None)
        if self._runner is not None:
            outcome = self._runner(argv, cwd=cwd)
            if isinstance(outcome, BridgeResult):
                return outcome
            return BridgeResult("ok", output=str(outcome))
        completed = subprocess.run(argv, shell=False, capture_output=True,
                                   text=True, timeout=EXEC_TIMEOUT_S,
                                   cwd=str(cwd) if cwd else None)
        output = (completed.stdout or "") + (completed.stderr or "")
        if completed.returncode != 0:
            return BridgeResult("error", output=output[:4000],
                                error=f"exit code {completed.returncode}")
        return BridgeResult("ok", output=output[:4000])
