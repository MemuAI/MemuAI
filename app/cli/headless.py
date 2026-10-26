"""``memucode -p "task"``: run one request without the interactive interface.

For scripts, CI and pipes (``git diff | memucode -p "review this"``).
Nothing asks the user: actions that would need approval are refused unless
``--permission-mode`` or ``--allowedTools`` permit them. Output formats:

* ``text`` — the answer, streamed as it is written;
* ``json`` — one object with the answer, usage, cost and session id;
* ``stream-json`` — one JSON event per line (text deltas, tool calls, result).
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict, List

from .. import pricing
from .ui_base import BaseUI

class HeadlessUI(BaseUI):
    interactive = False

    def __init__(self, fmt: str = "text", verbose: bool = False) -> None:
        self.fmt = fmt
        self.verbose = verbose
        self.out = sys.stdout
        self.err = sys.stderr
        self.last_text = ""

        self._held = ""
        self._seg_start = True
        self._seg_wrote = False
        self._had_segment = False

    def _progress(self, text: str) -> None:
        """One dim line on an interactive stderr while tools run (stdout stays the clean answer)."""
        if self.fmt != "text" or self.verbose:
            return
        try:
            if not self.err.isatty():
                return
        except Exception:
            return
        self.err.write(f"\x1b[2m  › {text[:160]}\x1b[0m\n")
        self.err.flush()

    def _event(self, payload: Dict[str, Any]) -> None:
        if self.fmt == "stream-json":
            self.out.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
            self.out.flush()

    def _note(self, text: str) -> None:
        if self.verbose:
            self.err.write(text.rstrip() + "\n")
            self.err.flush()

    def status(self, text: str) -> None:
        self._note(f"· {text}")

    def notice(self, text: str, level: str = "info") -> None:
        self._note(f"[{level}] {text}")
        self._event({"type": "notice", "level": level, "text": text})

    def stream_text(self, agent: Any, text: str) -> None:
        if getattr(agent, "depth", 0):
            return
        if self.fmt == "text":
            chunk = text
            if self._seg_start:
                chunk = chunk.lstrip("\n")
                if chunk:
                    if self._had_segment:
                        self.out.write("\n")
                    self._seg_start = False
            if chunk:
                combined = self._held + chunk
                body = combined.rstrip("\n")
                self._held = combined[len(body):]
                if body:
                    self.out.write(body)
                    self._seg_wrote = True
                self.out.flush()
        self._event({"type": "text", "text": text})

    def stream_reasoning(self, agent: Any, text: str) -> None:
        self._event({"type": "reasoning", "text": text})

    def stream_finished(self, agent: Any, text: str, reasoning: str, usage: Any) -> None:
        if getattr(agent, "depth", 0):
            return
        if text:
            self.last_text = text
        if self.fmt == "text" and self._seg_wrote:
            self.out.write("\n")
            self.out.flush()
            self._had_segment = True
        self._held = ""
        self._seg_start = True
        self._seg_wrote = False

    def tool_started(self, agent: Any, call: Any) -> None:
        self._note(f"→ {call.name}({call.display})")
        if not getattr(agent, "depth", 0):
            self._progress(f"{call.name} {call.display}".strip())
        self._event({"type": "tool_use", "id": call.id, "name": call.name, "input": call.args})

    def tool_finished(self, agent: Any, call: Any, result: Any) -> None:
        self._note(f"  {'✓' if result.ok else '✗'} {result.summary or result.error}")
        self._event({"type": "tool_result", "id": call.id, "name": call.name, "ok": result.ok,
                     "summary": result.summary, "error": result.error})

    def todos_changed(self, todos: List[Dict[str, Any]]) -> None:
        self._event({"type": "todos", "todos": todos})

async def run(runtime: Any, prompt: str, *, fmt: str = "text", verbose: bool = False,
              system_append: str = "") -> int:
    try:
        return await _run(runtime, prompt, fmt=fmt, verbose=verbose, system_append=system_append)
    finally:

        await runtime.shutdown()

async def _run(runtime: Any, prompt: str, *, fmt: str, verbose: bool, system_append: str) -> int:
    from .agent import Agent
    ui = HeadlessUI(fmt, verbose)
    runtime.ui = ui
    started = time.monotonic()
    await runtime.startup()
    if not runtime.model:
        message = "No model is available: start MemuAI Proxy or configure an API (memucode, then /proxy add)."
        if fmt == "text":
            sys.stderr.write(message + "\n")
        else:
            print(json.dumps({"type": "result", "subtype": "error", "is_error": True, "result": message}))
        return 2
    if system_append:
        runtime.session_context.append(system_append)
    content = prompt
    if runtime.session_context:
        content += "\n\n<session_context>\n" + "\n".join(runtime.session_context) + "\n</session_context>"
    ui._event({"type": "system", "subtype": "init", "session_id": runtime.session.id, "model": runtime.model,
               "cwd": str(runtime.root), "permission_mode": runtime.permissions.mode,
               "tools": [tool.name for tool in runtime.tools.available(runtime)]})
    agent = Agent(runtime)
    result = await agent.run_turn(content)
    while True:
        follow = await runtime.goal_followup(result)
        if not follow:
            break
        result = await agent.run_turn(follow, kind="command", display="↻ goal")
    costs = runtime.stats.costs(runtime.settings.get("pricing.overrides") or {})
    total = runtime.stats.total()
    summary = {
        "type": "result", "subtype": "success" if result.stopped == "done" else result.stopped,
        "is_error": result.stopped != "done", "result": result.text or result.error,
        "session_id": runtime.session.id, "model": runtime.model, "num_turns": result.rounds,
        "error_category": getattr(result, "error_category", "") or None,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "usage": {"input_tokens": total.input, "output_tokens": total.output, "cache_read_input_tokens": total.cache_read,
                  "estimated": total.estimated},
        "total_cost_usd": round(costs["api_equivalent"], 6), "billed_cost_usd": round(costs["actual"], 6),
    }
    if fmt == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif fmt == "stream-json":
        ui._event(summary)
    elif result.stopped != "done" and result.error:
        sys.stderr.write(f"Error: {result.error}\n")
    if verbose and fmt == "text":
        sys.stderr.write(f"[{summary['num_turns']} rounds · {total.input}↑ {total.output}↓ tokens · "
                         f"{pricing.format_usd(costs['api_equivalent'])} · session {runtime.session.id}]\n")
    return 0 if result.stopped == "done" else 1
