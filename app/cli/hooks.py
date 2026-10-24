"""Hooks: shell commands MemuCode runs at fixed points of its loop.

The configuration format is the one Claude Code uses, so existing hooks work::

    "hooks": {
      "PostToolUse": [{"matcher": "Edit|Write", "hooks": [
          {"type": "command", "command": "npx prettier --write \\"$MEMUCODE_FILE\\"", "timeout": 60}]}]
    }

Events: PreToolUse, PostToolUse, UserPromptSubmit, Stop, SubagentStop,
SessionStart, SessionEnd, PreCompact, Notification. The hook receives a JSON
object on stdin. Exit code 2 blocks (its stderr goes back to the model or the
user); JSON on stdout may carry ``decision``/``reason``, ``continue``,
``hookSpecificOutput.permissionDecision`` and ``additionalContext``.
Project hooks run only in a folder the user trusted.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:  # pragma: no cover
    from .runtime import Runtime

EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SubagentStop", "SessionStart",
          "SessionEnd", "PreCompact", "Notification")


@dataclass
class HookOutcome:
    decision: str = ""               # allow | deny | ask | block
    reason: str = ""
    context: List[str] = field(default_factory=list)
    stop: bool = False
    stop_reason: str = ""
    messages: List[str] = field(default_factory=list)   # shown to the user
    ran: int = 0


class HookRunner:
    def __init__(self, runtime: "Runtime") -> None:
        self.runtime = runtime
        self.last_errors: List[str] = []

    def entries(self, event: str) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        trusted = self.runtime.settings.is_trusted()
        for name, _path, layer in self.runtime.settings.layers[1:]:
            if name in ("project", "local") and not trusted:
                continue
            hooks = (layer.get("hooks") or {}) if isinstance(layer, dict) else {}
            for block in hooks.get(event) or []:
                if isinstance(block, dict):
                    rows.append({**block, "_source": name})
        return rows

    def count(self) -> int:
        return sum(len(self.entries(event)) for event in EVENTS)

    @staticmethod
    def _matches(matcher: str, value: str) -> bool:
        matcher = str(matcher or "").strip()
        if not matcher or matcher == "*":
            return True
        try:
            return re.fullmatch(matcher, value or "") is not None
        except re.error:
            return matcher == value

    async def run(self, event: str, payload: Dict[str, Any], *, match: str = "") -> HookOutcome:
        outcome = HookOutcome()
        blocks = [block for block in self.entries(event) if self._matches(block.get("matcher", ""), match)]
        if not blocks:
            return outcome
        base = {"session_id": self.runtime.session.id, "transcript_path": str(self.runtime.session.path),
                "cwd": str(self.runtime.session.cwd or self.runtime.root), "hook_event_name": event,
                "permission_mode": self.runtime.permissions.mode}
        body = json.dumps({**base, **payload}, ensure_ascii=False, default=str)
        for block in blocks:
            for hook in block.get("hooks") or []:
                if not isinstance(hook, dict) or hook.get("type", "command") != "command":
                    continue
                command = str(hook.get("command") or "").strip()
                if not command:
                    continue
                outcome.ran += 1
                code, stdout, stderr = await self._exec(command, body, payload, float(hook.get("timeout") or 60))
                self._interpret(event, code, stdout, stderr, outcome)
                if outcome.decision in ("deny", "block") or outcome.stop:
                    return outcome
        return outcome

    async def _exec(self, command: str, body: str, payload: Dict[str, Any], timeout: float):
        from .tools.shell import argv_for, command_env
        env = command_env(self.runtime)
        env["MEMUCODE_PROJECT_DIR"] = str(self.runtime.root)
        env["CLAUDE_PROJECT_DIR"] = str(self.runtime.root)
        tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        file_path = tool_input.get("file_path") or tool_input.get("path")
        if file_path:
            env["MEMUCODE_FILE"] = str(file_path)
        argv = argv_for(self.runtime.settings, command)

        def work():
            # run_bounded kills the hook's whole process tree on timeout:
            # subprocess.run only killed the shell and then waited forever on
            # the pipes a grandchild still held (Windows), freezing the turn.
            from .. import code_sandbox
            try:
                code, out, err, timed_out = code_sandbox.run_bounded(
                    argv, input_bytes=body.encode("utf-8"), cwd=str(self.runtime.root), env=env,
                    timeout=max(1.0, timeout))
            except OSError as exc:
                return (127, "", f"hook could not start: {exc}")
            if timed_out:
                return (124, "", f"hook timed out after {timeout:.0f}s: {command[:80]}")
            # a PowerShell/cmd hook may print cp866/cp1251
            return (code, code_sandbox.OutputDecoder().decode(out, final=True),
                    code_sandbox.OutputDecoder().decode(err, final=True))

        return await asyncio.to_thread(work)

    def _interpret(self, event: str, code: int, stdout: str, stderr: str, outcome: HookOutcome) -> None:
        data: Dict[str, Any] = {}
        text = stdout.strip()
        if text.startswith("{"):
            try:
                data = json.loads(text)
            except ValueError:
                data = {}
        if code == 2:
            reason = stderr.strip() or text or "blocked by a hook"
            outcome.decision = "deny" if event == "PreToolUse" else "block"
            outcome.reason = reason[:4000]
            return
        if code != 0:
            message = (stderr.strip() or text or f"exit code {code}")[:600]
            self.last_errors.append(f"{event}: {message}")
            outcome.messages.append(f"hook ({event}) failed: {message}")
            return
        if data:
            if data.get("continue") is False:
                outcome.stop = True
                outcome.stop_reason = str(data.get("stopReason") or "")
            decision = str(data.get("decision") or "").lower()
            if decision in ("block", "deny"):
                outcome.decision = "deny" if event == "PreToolUse" else "block"
                outcome.reason = str(data.get("reason") or "")
            elif decision in ("approve", "allow"):
                outcome.decision = "allow"
            specific = data.get("hookSpecificOutput") if isinstance(data.get("hookSpecificOutput"), dict) else {}
            permission = str(specific.get("permissionDecision") or "").lower()
            if permission in ("allow", "deny", "ask"):
                outcome.decision = permission
                outcome.reason = str(specific.get("permissionDecisionReason") or outcome.reason)
            extra = specific.get("additionalContext") or data.get("additionalContext")
            if extra:
                outcome.context.append(str(extra)[:6000])
            if data.get("systemMessage"):
                outcome.messages.append(str(data["systemMessage"])[:600])
        elif text and event in ("UserPromptSubmit", "SessionStart"):
            outcome.context.append(text[:6000])
