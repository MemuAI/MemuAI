"""The interface an agent reports to. The terminal UI, the headless printer
(``-p``) and sub-agent relays implement it; every method has a quiet default."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class BaseUI:
    interactive = False

    # --- progress
    def status(self, text: str) -> None: ...
    def mode_changed(self, mode: str) -> None: ...
    def notice(self, text: str, level: str = "info") -> None: ...
    def compacted(self, result: Dict[str, Any]) -> None: ...

    # --- one turn
    def turn_started(self, agent: Any, message: Dict[str, Any]) -> None: ...
    def stream_started(self, agent: Any) -> None: ...
    def stream_text(self, agent: Any, text: str) -> None: ...
    def stream_reasoning(self, agent: Any, text: str) -> None: ...
    def stream_status(self, agent: Any, phase: str, data: Dict[str, Any]) -> None: ...
    def stream_block(self, agent: Any, what: str, chars: int) -> None: ...
    def stream_finished(self, agent: Any, text: str, reasoning: str, usage: Any) -> None: ...
    def tool_started(self, agent: Any, call: Any) -> None: ...
    def tool_progress(self, agent: Any, call: Any, text: str) -> None: ...
    def tool_finished(self, agent: Any, call: Any, result: Any) -> None: ...
    def todos_changed(self, todos: List[Dict[str, Any]]) -> None: ...
    def turn_finished(self, agent: Any, result: Any) -> None: ...
    def background_finished(self, job: Dict[str, Any]) -> None: ...

    # --- questions
    async def ask_permission(self, request: Any) -> Dict[str, Any]:
        return {"choice": "no"}

    async def ask_question(self, question: str, options: List[str], multi: bool = False) -> Optional[str]:
        return None

    async def approve_plan(self, plan: str) -> Dict[str, Any]:
        return {"choice": "keep"}

    async def confirm(self, title: str, body: str = "", danger: bool = False) -> bool:
        return False

    def take_queued(self) -> List[Dict[str, Any]]:
        return []


class RelayUI(BaseUI):
    """UI of a sub-agent: tool activity becomes progress lines under the parent
    Task call, questions go to the real UI, streamed text stays hidden."""

    def __init__(self, parent: BaseUI, parent_agent: Any, parent_call: Any, label: str = "") -> None:
        self.parent = parent
        self.parent_agent = parent_agent
        self.parent_call = parent_call
        self.label = label
        self.interactive = parent.interactive
        self.tools_used = 0

    def _line(self, text: str) -> None:
        self.parent.tool_progress(self.parent_agent, self.parent_call, text + "\n")

    def status(self, text: str) -> None:
        self._line("· " + text)

    def notice(self, text: str, level: str = "info") -> None:
        self._line(("! " if level != "info" else "· ") + text)

    def tool_started(self, agent: Any, call: Any) -> None:
        self.tools_used += 1
        self._line(f"{call.name}({call.display})" if call.display else call.name)

    def tool_finished(self, agent: Any, call: Any, result: Any) -> None:
        if result is not None and not result.ok:
            self._line("  ✗ " + (result.error or result.summary or "error")[:160])

    async def ask_permission(self, request: Any) -> Dict[str, Any]:
        return await self.parent.ask_permission(request)

    async def confirm(self, title: str, body: str = "", danger: bool = False) -> bool:
        return await self.parent.confirm(title, body, danger)

    def todos_changed(self, todos: List[Dict[str, Any]]) -> None:
        return None
