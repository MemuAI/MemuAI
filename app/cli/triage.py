"""A look at the request before the work starts, by the decision model (when one is connected).

Three of the things a decision model is used for are about the *request* rather than an action:

* ``routing`` — how hard is it, so which model answers (``/model auto``);
* ``skills``  — does one of the skills fit, a hint for the model and a line for the user;
* ``intent``  — is it a big job that deserves a plan first.

The answer is needed before the first request goes out, so the questions of every use that is switched on and
shares a decision model are asked together — one round trip, not three. Uses that were given different models
(``/decisions``) are asked in parallel, each its own model. The wait is short: a decision model answers in
tenths of a second, and a slow or failing one must never hold the work up — the request simply goes ahead
without that hint.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

TIMEOUT = 4.0

SKILL_THRESHOLD = 0.5

PLAN_THRESHOLD = 0.8

_PARTS = {"routing": ("complexity",), "skills": ("skill",), "intent": ("intent", "needs_plan")}

@dataclass
class Triage:
    complexity: Optional[float] = None
    intent: str = ""
    needs_plan: Optional[float] = None
    skill: Optional[Dict[str, Any]] = None
    models: List[str] = field(default_factory=list)
    seconds: float = 0.0
    cost: float = 0.0

    @property
    def wants_plan(self) -> bool:
        return (self.needs_plan or 0.0) >= PLAN_THRESHOLD and self.intent in ("large_change", "investigation")

def request_text(content: Any) -> str:
    """What the person wrote: the text of a message, without the context MemuCode attached to it."""
    if isinstance(content, list):
        content = "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    text = str(content or "")
    marker = text.find("<session_start_context>")
    return (text[:marker] if marker >= 0 else text).strip()

def worth_looking_at(text: str) -> bool:
    """Greetings and one-word answers are not analysed: there is nothing to route, hint or plan."""
    return len(text) >= 12 and not text.startswith(("/", "!", "#"))

async def triage(runtime: Any, prompt: str, *, routing: bool = False, skills: bool = False,
                 intent: bool = False) -> Optional[Triage]:
    """The decision model's view of ``prompt`` for the uses asked for (and switched on in ``/decisions``).

    None when no decision model is connected, nothing is switched on, or nothing came back in time."""
    from ..decisions import analyze_request
    decisions = runtime.decisions
    if not decisions.enabled or decisions.provider == "model":
        return None
    uses = runtime.settings.get("decisions.uses") or {}
    wanted = {"routing": routing and bool(uses.get("routing", True)),
              "skills": skills and bool(uses.get("skills", True)),
              "intent": intent and bool(uses.get("intent", True))}
    catalogue: Dict[str, str] = {}
    if wanted["skills"]:
        loaded = set(getattr(runtime.session, "active_skills", ()) or ())
        for row in runtime.skills.catalogue():
            if row["id"] not in loaded:
                catalogue[row["id"]] = str(row.get("description") or row.get("title") or row["id"])
        if not catalogue:
            wanted["skills"] = False
    groups: Dict[str, List[str]] = {}
    for use, on in wanted.items():
        if on:
            groups.setdefault(decisions.model_for(use), []).append(use)
    if not groups:
        return None
    context = runtime.session.brief_context(1500)
    started = time.monotonic()

    async def ask(group: List[str]) -> Dict[str, Any]:
        parts = [part for use in group for part in _PARTS[use]]
        return await analyze_request(decisions.for_use(group[0]), prompt, context=context,
                                     skills=catalogue if "skills" in group else None, want=parts)

    tasks = [asyncio.ensure_future(ask(group)) for group in groups.values()]
    done, pending = await asyncio.wait(tasks, timeout=TIMEOUT)
    for task in pending:
        task.cancel()
    result = Triage(seconds=time.monotonic() - started)
    for task in done:
        if task.cancelled() or task.exception() is not None:
            continue
        answer = task.result()
        runtime.stats.decision_calls += 1
        runtime.stats.decision_cost += float(answer.get("cost") or 0.0)
        result.cost += float(answer.get("cost") or 0.0)
        result.models.append(str(answer.get("model") or ""))
        if answer.get("complexity") is not None:
            result.complexity = float(answer["complexity"])
        if answer.get("intent"):
            result.intent = str(answer["intent"])
        if answer.get("needs_plan") is not None:
            result.needs_plan = float(answer["needs_plan"])
        skill = answer.get("skill")
        if skill and float(skill.get("probability") or 0.0) >= SKILL_THRESHOLD and skill.get("skill") in catalogue:
            result.skill = skill
    if (result.complexity is None and result.needs_plan is None and not result.intent and not result.skill
            and not result.models):
        return None
    return result
