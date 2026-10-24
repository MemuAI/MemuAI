"""Agent tools that talk to the user or to other models: TodoWrite,
AskUserQuestion, ExitPlanMode, Skill, Task (sub-agents), Advisor and Decide.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from ..i18n import tr
from .base import Tool, ToolError, ToolResult, arg, flag, items, structured, whole

TODO_STATES = ("pending", "in_progress", "completed")

def _agents_in_play(runtime) -> bool:
    """Round 32 (tokens): the agent-management tools are listed only when agents can matter in this turn — the
    user asked for them, the model may start them on its own (auto), or one exists already."""
    from ..prompts import subagent_mode, user_asked_for_agents
    mode = subagent_mode(runtime)
    if mode == "off":
        return False
    if mode == "auto" or getattr(getattr(runtime, "jobs", None), "jobs", None):
        return True
    try:
        return user_asked_for_agents(runtime)
    except Exception:
        return True



class TodoWriteTool(Tool):
    name = "TodoWrite"
    kind = "meta"
    short = "replace the task checklist {todos: [{content, status: pending|in_progress|completed, activeForm?}]}"
    description = (
        "Creates or updates the visible task list for the current request. Use it for work with three or "
        "more steps: write all steps first, keep exactly one step in_progress, mark each completed "
        "immediately when it is done. Always send the full list. Do not use it for trivial one-step tasks.")
    parameters = {"type": "object", "properties": {"todos": {"type": "array", "items": {
        "type": "object", "properties": {
            "content": {"type": "string", "description": "Imperative: 'Run the tests'"},
            "status": {"type": "string", "enum": list(TODO_STATES)},
            "activeForm": {"type": "string", "description": "Present continuous: 'Running the tests'"}},
        "required": ["content", "status"]}}}}
    required = ("todos",)

    def display(self, args):
        return ""

    async def run(self, ctx, args, prepared):
        raw = structured(args.get("todos"))
        if isinstance(raw, dict):                      # {"todos": [...]} inside the argument, or one todo object
            inner = structured(raw.get("todos"))
            raw = inner if isinstance(inner, list) else [raw]
        if isinstance(raw, str) and raw.strip():       # a plain list of lines
            raw = [line.strip(" -*•\t") for line in raw.splitlines() if line.strip(" -*•\t")]
        if not isinstance(raw, list):
            raise ToolError("todos must be a list of {content, status}.")
        raw = [structured(item) for item in raw]
        todos: List[Dict[str, Any]] = []
        for index, item in enumerate(raw[:60]):
            if isinstance(item, str):
                item = {"content": item, "status": "pending"}
            if not isinstance(item, dict) or not str(item.get("content") or "").strip():
                continue
            status = str(item.get("status") or "pending").lower().replace(" ", "_").replace("-", "_")
            status = {"done": "completed", "complete": "completed", "in progress": "in_progress",
                      "active": "in_progress", "todo": "pending"}.get(status, status)
            if status not in TODO_STATES:
                status = "pending"
            todos.append({"id": str(item.get("id") or index + 1), "content": str(item["content"]).strip()[:300],
                          "status": status, "activeForm": str(item.get("activeForm") or "").strip()[:300]})
        running = [todo for todo in todos if todo["status"] == "in_progress"]
        for extra in running[1:]:
            extra["status"] = "pending"
        ctx.session.todos = todos
        ctx.interface.todos_changed(todos)
        done = sum(1 for todo in todos if todo["status"] == "completed")
        note = "" if len(running) <= 1 else " (only the first in_progress item was kept in progress)"
        return ToolResult(ok=True, output=f"Todo list updated: {done}/{len(todos)} completed{note}.",
                          summary=tr(f"Задачи: {done}/{len(todos)}", f"Todos: {done}/{len(todos)}"),
                          meta={"todos": todos})


class AskUserTool(Tool):
    name = "AskUserQuestion"
    kind = "meta"
    short = "ask the user a question with options {question, options?: [..], multi_select?}"
    description = (
        "Asks the user a clarifying question when a decision genuinely belongs to them (ambiguous "
        "requirements, a trade-off, missing credentials). Offer 2-4 concrete options when possible; the "
        "user can always type their own answer. Do not ask what you can find out with tools.")
    parameters = {"type": "object", "properties": {
        "question": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"}},
        "multi_select": {"type": "boolean"}}}
    required = ("question",)

    def display(self, args):
        return str(args.get("question") or "")[:90]

    async def run(self, ctx, args, prepared):
        question = str(arg(args, "question", "text", default="")).strip()
        if not question:
            raise ToolError("question is required")
        options = [str(item)[:200] for item in items(args.get("options")) if str(item).strip()][:8]
        if not options and isinstance(args.get("questions"), list):
            first = args["questions"][0] if args["questions"] else {}
            question = str(first.get("question") or question)
            options = [str(row.get("label") if isinstance(row, dict) else row) for row in first.get("options") or []]
        answer = await ctx.interface.ask_question(question, options, flag(args.get("multi_select")))
        if answer is None:
            return ToolResult(ok=False, error="The user dismissed the question without answering.",
                              summary=tr("Вопрос отклонён", "Question dismissed"))
        return ToolResult(ok=True, output=f"The user answered: {answer}", summary=str(answer)[:120])


class ExitPlanModeTool(Tool):
    name = "ExitPlanMode"
    kind = "meta"
    short = "present the finished plan for approval (plan mode only) {plan}"
    description = (
        "In plan mode, call this once your read-only research is done and the plan is ready. The plan "
        "(markdown, concise, concrete steps with files) is shown to the user, who approves it or asks "
        "for changes. Only use it for implementation plans, not for research answers.")
    parameters = {"type": "object", "properties": {"plan": {"type": "string"}}}
    required = ("plan",)

    def enabled(self, runtime):
        return runtime.permissions.mode == "plan"

    def display(self, args):
        return ""

    async def run(self, ctx, args, prepared):
        plan = str(arg(args, "plan", "text", default="")).strip()
        if not plan:
            raise ToolError("plan is required")
        decision = await ctx.interface.approve_plan(plan)
        choice = decision.get("choice") if isinstance(decision, dict) else decision
        ctx.session.plan = plan
        if choice in ("auto", "manual"):
            # Plan approval is about this task: the mode the other windows share is left alone.
            ctx.runtime.permissions.set_mode("acceptEdits" if choice == "auto" else "default", source="plan")
            return ToolResult(ok=True, output=("The user approved the plan. Plan mode is off; implement the plan "
                                               "now, step by step, tracking it with TodoWrite."),
                              summary=tr("План одобрен", "Plan approved"))
        feedback = str(decision.get("feedback") or "") if isinstance(decision, dict) else ""
        return ToolResult(ok=True, output=("The user did not approve the plan yet. Stay in plan mode, revise "
                                           "the plan and present it again with ExitPlanMode."
                                           + (f" Their feedback: {feedback}" if feedback else "")),
                          summary=tr("Нужны правки плана", "Plan needs changes"))


class SkillTool(Tool):
    name = "Skill"
    kind = "meta"
    parallel = True
    short = "load a skill playbook by name {name}"
    description = ("Loads the instructions of a skill (a playbook for a kind of task: review, tests, "
                   "debugging, release…). Load a skill when its description matches the task.")
    parameters = {"type": "object", "properties": {"name": {"type": "string"},
                                                   "args": {"type": "string"}}}
    required = ("name",)

    async def run(self, ctx, args, prepared):
        name = str(arg(args, "name", "skill", "id", default="")).strip().lower()
        skill = ctx.runtime.skills.get(name)
        if skill is None:
            names = ", ".join(row["id"] for row in ctx.runtime.skills.catalogue()[:40])
            raise ToolError(f"Unknown skill {name!r}. Available: {names}")
        ctx.session.active_skills.add(skill["id"])
        return ToolResult(ok=True, output=f"<skill name=\"{skill['id']}\">\n{skill['prompt']}\n</skill>\n"
                                          "Follow these instructions for the current task.",
                          summary=tr(f"Скилл «{skill['id']}» загружен", f"Loaded skill {skill['id']}"))


class TaskTool(Tool):
    name = "Task"
    kind = "agent"
    parallel = True
    short = ("start a sub-agent {description, prompt, name?, role?, subagent_type?, tools?, model?, "
             "run_in_background?}")
    parameters = {"type": "object", "properties": {
        "description": {"type": "string", "description": "3-5 words"},
        "prompt": {"type": "string", "description": "Complete, self-contained task and what to report back"},
        "name": {"type": "string", "description": "Short handle to address the agent later, e.g. \"tests\""},
        "role": {"type": "string", "description": "Its role in the team, e.g. \"read-only explorer of app/api\""},
        "subagent_type": {"type": "string", "description": "Agent type (default general-purpose)"},
        "tools": {"type": "array", "items": {"type": "string"},
                  "description": "Only these tools (e.g. [\"Read\",\"Grep\"]); omit for the type's default"},
        "model": {"type": "string", "description": "One of the agent models from the system prompt (omit = default)"},
        "run_in_background": {"type": "boolean",
                              "description": "Return at once; manage it with Agents (send/wait/list/cancel)"}}}
    required = ("description", "prompt")

    @property
    def description(self) -> str:  # type: ignore[override]
        return ("Launches a sub-agent with its own fresh, isolated context for a self-contained task. It cannot see "
                "this conversation: the prompt must hold everything it needs and say what to report. Give it a "
                "name and a role. Several Task calls in one turn run in parallel. With run_in_background=true you "
                "keep working; then use Agents to send follow-up instructions, wait for one/any/all, list or "
                "cancel. By default allowed only when the user asked for agents or parallel work.")

    def enabled(self, runtime):
        from ..prompts import subagent_mode
        return subagent_mode(runtime) != "off"

    def display(self, args):
        kind = str(args.get("subagent_type") or "")
        text = str(args.get("description") or args.get("prompt") or "")[:80]
        name = str(args.get("name") or "").strip()
        if name:
            text = f"{name}: {text}"
        return f"{kind}: {text}" if kind and kind != "general-purpose" else text

    async def run(self, ctx, args, prepared):
        if ctx.depth >= 1:
            raise ToolError("Sub-agents cannot start further sub-agents; do the work directly.")
        from ..prompts import subagent_mode, user_asked_for_agents
        if subagent_mode(ctx.runtime) == "ask" and not user_asked_for_agents(ctx.runtime):
            # The rule is enforced here and not only in the prompt: models start agents "to be thorough".
            raise ToolError("Sub-agents run only when the user asks for them (agents, workers, parallel or background "
                            "work). The user did not ask for that here: do this work yourself, with your own tools.")
        prompt = str(arg(args, "prompt", "task", default="")).strip()
        if not prompt:
            raise ToolError("prompt is required")
        kind = str(args.get("subagent_type") or "general-purpose")
        role = str(args.get("role") or "").strip()
        tools = [str(item) for item in items(args.get("tools"))] if args.get("tools") else None
        if flag(args.get("run_in_background")):
            try:
                job = ctx.runtime.jobs.start(prompt, kind=kind, origin="model",
                                             description=str(args.get("description") or ""),
                                             model=str(args.get("model") or ""), name=str(args.get("name") or ""),
                                             role=role, tools=tools)
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            return ToolResult(ok=True, output=(f"Task #{job['id']} is running in the background as \"{job['name']}\". "
                                               "Keep working; its report is delivered to you automatically when it "
                                               "finishes. Agents(action=send|wait|list|cancel, agent="
                                               f"\"{job['name']}\") manages it; TaskOutput(task_id={job['id']}) "
                                               "reads it."),
                              summary=f"Agent #{job['id']} {job['name']} started in the background",
                              meta={"task_id": job["id"], "name": job["name"]})
        result = await ctx.runtime.run_subagent(kind, str(args.get("description") or ""), prompt, ctx,
                                                requested_model=str(args.get("model") or ""), role=role, tools=tools)
        return ToolResult(ok=result.get("ok", True), output=result.get("report") or "(no report)",
                          summary=result.get("summary") or tr("Готово", "Done"),
                          error=result.get("error", ""), meta={"usage": result.get("usage")})


class TaskOutputTool(Tool):
    name = "TaskOutput"
    kind = "meta"
    parallel = True
    short = "read or wait for a background agent {task_id, wait_seconds?}"
    description = ("Read a background agent's status and full report (task_id = its number or name). Optionally "
                   "wait up to 60 seconds. Cancelling a wait does not stop the agent.")
    parameters = {"type": "object", "properties": {
        "task_id": {"type": "string", "description": "The number (or name) Task returned"},
        "wait_seconds": {"type": "number", "description": "Wait up to this many seconds (0-60) for the report"}}}
    required = ("task_id",)

    def enabled(self, runtime):
        return _agents_in_play(runtime)

    async def run(self, ctx, args, prepared):
        import asyncio
        job = ctx.runtime.jobs.find(args.get("task_id"), ctx.session.id)
        if job is None:
            raise ToolError("No background agent with that ID in this session")
        wait = whole(args.get("wait_seconds"), 0, "wait_seconds", low=0, high=60)
        if wait and job["status"] == "running":
            try:
                await asyncio.wait_for(asyncio.shield(job["task"]), wait)
            except asyncio.TimeoutError:
                pass
        if job["status"] != "running":
            ctx.runtime.discard_note(f"job:{job['id']}")     # the model has the report now: no second copy
        return ToolResult(ok=True, output={"task_id": job["id"], "name": job.get("name", ""), "status": job["status"],
                                          "report": job["result"], "log": job["log"][-20:]},
                          summary=f"Agent #{job['id']}: {job['status']}")


class AgentsTool(Tool):
    """Round 32: manage the background agents the way a lead manages helpers."""
    name = "Agents"
    kind = "meta"
    parallel = True
    short = "manage background agents {action: list|send|wait|cancel, agent?, agents?, message?, mode?, timeout?}"
    description = ("Manage background agents started with Task(run_in_background=true). action=list: status of "
                   "each. send: follow-up instructions to one agent (agent + message) — a running agent gets them "
                   "after its current step, a finished one resumes with its context. wait: block until any or all "
                   "of `agents` (default: every running one) finish, up to `timeout` seconds (default 120, max "
                   "600); returns their reports. cancel: stop one agent.")
    parameters = {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["list", "send", "wait", "cancel"]},
        "agent": {"type": "string", "description": "Number or name (send, cancel; or a single one to wait for)"},
        "agents": {"type": "array", "items": {"type": "string"}, "description": "wait: numbers or names"},
        "message": {"type": "string", "description": "send: the follow-up instructions"},
        "mode": {"type": "string", "enum": ["any", "all"], "description": "wait: return on the first or on all"},
        "timeout": {"type": "number", "description": "wait: seconds (default 120, max 600)"}}}
    required = ("action",)

    def enabled(self, runtime):
        return _agents_in_play(runtime)

    def display(self, args):
        action = str(args.get("action") or "list")
        target = args.get("agent") or ", ".join(str(item) for item in items(args.get("agents")) or [])
        return f"{action} {target}".strip()

    async def run(self, ctx, args, prepared):
        if ctx.depth >= 1:
            raise ToolError("Only the main agent manages agents.")
        jobs = ctx.runtime.jobs
        session_id = ctx.session.id
        action = str(args.get("action") or "list").strip().lower()
        if action == "list":
            rows = [jobs.summary(job, report_chars=400) for job in jobs.jobs if job.get("session_id") == session_id]
            if not rows:
                return ToolResult(ok=True, output="No agents in this session.", summary=tr("Агентов нет", "No agents"))
            running = sum(1 for row in rows if row["status"] == "running")
            return ToolResult(ok=True, output=rows, summary=f"{len(rows)} agents, {running} running")
        if action == "send":
            try:
                outcome = jobs.send(args.get("agent"), str(args.get("message") or ""), session_id=session_id)
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            job = outcome["job"]
            how = ("queued: it reads it after its current step" if outcome["queued"]
                   else "it resumed with its previous context")
            return ToolResult(ok=True, output=f"Sent to #{job['id']} {job['name']} — {how}. Its report arrives by "
                                              "itself, or use Agents(action=wait).",
                              summary=f"→ #{job['id']} {job['name']}")
        if action == "cancel":
            job = jobs.find(args.get("agent"), session_id)
            if job is None:
                raise ToolError(f"No agent {args.get('agent')!r} in this session")
            stopped = jobs.cancel(job["id"])
            return ToolResult(ok=True, output=(f"#{job['id']} {job['name']} cancelled." if stopped
                                               else f"#{job['id']} {job['name']} was not running ({job['status']})."),
                              summary=f"#{job['id']} {'cancelled' if stopped else job['status']}")
        if action == "wait":
            refs = [str(item) for item in items(args.get("agents"))] if args.get("agents") else []
            if args.get("agent"):
                refs.append(str(args.get("agent")))
            mode = "any" if str(args.get("mode") or "all").lower() == "any" else "all"
            timeout = float(whole(args.get("timeout"), 120, "timeout", low=0, high=600))
            try:
                rows = await jobs.wait(refs, mode=mode, timeout=timeout, session_id=session_id)
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            if not rows:
                return ToolResult(ok=True, output="No agents are running.", summary=tr("Нечего ждать", "Nothing to wait for"))
            for job in rows:
                if job["status"] != "running":
                    ctx.runtime.discard_note(f"job:{job['id']}")     # delivered here: no second copy later
            out = [jobs.summary(job) for job in rows]
            done = sum(1 for job in rows if job["status"] != "running")
            return ToolResult(ok=True, output=out, summary=f"{done}/{len(rows)} finished")
        raise ToolError("action must be list, send, wait or cancel")


class AdvisorTool(Tool):
    name = "Advisor"
    kind = "meta"
    short = "ask a stronger advisor model for a second opinion {question, context?}"
    description = ("Consults the advisor model (a stronger model chosen by the user) for a second opinion "
                   "on a hard decision: architecture, a tricky bug after two failed attempts, risky "
                   "trade-offs. Give it a precise question and the relevant facts; it sees the recent "
                   "conversation.")
    parameters = {"type": "object", "properties": {"question": {"type": "string"},
                                                   "context": {"type": "string"}}}
    required = ("question",)

    def enabled(self, runtime):
        return bool(runtime.settings.get("advisor_model"))

    async def run(self, ctx, args, prepared):
        question = str(arg(args, "question", default="")).strip()
        answer = await ctx.runtime.consult_advisor(question, str(args.get("context") or ""))
        return ToolResult(ok=True, output=f"Advisor ({ctx.runtime.settings.get('advisor_model')}):\n{answer}",
                          summary=tr("Совет получен", "Advice received"), detail=answer[:2000])


class DecideTool(Tool):
    name = "Decide"
    kind = "meta"
    parallel = True
    short = ("fast calibrated classifier (decision model) {state, question, type: noul|choice|score, "
             "options?, levels?}")
    description = (
        "Asks the configured decision model (Jev) a typed question about a text or JSON state and returns "
        "calibrated numbers in ~0.1-0.5 s: noul = probability a statement is true, choice = best option "
        "with probabilities, score = position on 2-10 ordered levels. Useful to classify many items "
        "(log lines, issues, test names) cheaply. It does not generate text.")
    parameters = {"type": "object", "properties": {
        "state": {"type": "string", "description": "Text (or JSON as a string) to judge"},
        "question": {"type": "string"},
        "type": {"type": "string", "enum": ["noul", "choice", "score"]},
        "options": {"type": "array", "items": {"type": "string"}, "description": "choice: the options"},
        "levels": {"type": "array", "items": {"type": "string"}, "description": "score: ordered levels"}}}
    required = ("state", "question")

    def enabled(self, runtime):
        return runtime.decisions.enabled and runtime.decisions.provider != "model"

    async def run(self, ctx, args, prepared):
        from ...decisions import DecisionsError, Question
        kind = str(args.get("type") or "noul").lower()
        question = str(args.get("question") or "")
        try:
            if kind == "choice":
                q = Question.choice(question, args.get("options") or [])
            elif kind == "score":
                q = Question.score(question, args.get("levels") or [])
            else:
                q = Question.noul(question)
            decision = await ctx.runtime.decisions.ask(args.get("state"), {"q": q})
        except DecisionsError as exc:
            raise ToolError(f"Decision model error: {exc}") from exc
        answer = decision["q"]
        ctx.runtime.stats.add_decision(decision)
        return ToolResult(ok=True, output=json.dumps(answer.to_dict(), ensure_ascii=False),
                          summary=f"{answer.type}: {answer.value} ({decision.latency_ms} ms)")


def register(registry) -> None:
    for tool in (TodoWriteTool(), AskUserTool(), ExitPlanModeTool(), SkillTool(), TaskTool(), TaskOutputTool(), AgentsTool(),
                 AdvisorTool(),
                 DecideTool()):
        registry.add(tool)
