"""The MemuCode runtime: one object that owns a session and everything the
agent needs — settings, model router, tools, permissions, hooks, MCP, the
decision model, usage statistics and costs, sub-agents and background jobs.
Both the terminal UI and the headless ``-p`` mode drive it.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import re
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from .. import pricing
from ..decisions import DecisionClient
from . import paths, project
from .hooks import HookRunner
from .i18n import tr
from .llm import ChatRequest, LLMError, ModelInfo, Router, SUBSCRIPTION_PROVIDERS, Usage
from .modelmeta import describe_candidates, model_notes, pick_by_notes, routing_config, worker_models
from .permissions import PermissionManager
from .session import Session
from .settings import Settings
from .tokens import estimate_text
from .tools import build_registry
from .tools.files import FileIndex
from .tools.mcp import CliMCP
from .tools.shell import BackgroundShells
from .ui_base import BaseUI, RelayUI

PROXY_WINDOW_CAP = 400_000

class Stats:
    """Usage and cost of this process's session."""

    def __init__(self) -> None:
        self.started = time.time()
        self.models: Dict[str, Dict[str, Any]] = {}
        self.api_seconds = 0.0
        self.tool_calls = 0
        self.tool_errors = 0
        self.lines_added = 0
        self.lines_removed = 0
        self.web_searches = 0
        self.decision_calls = 0
        self.decision_cost = 0.0
        self.decision_ms = 0
        self.compactions = 0
        self.turns = 0

    def add_usage(self, model: str, usage: Usage, *, provider: str = "", seconds: float = 0.0) -> None:
        if model.startswith("@"):

            price = pricing.lookup(model, provider)
            if price is not None and price.source == "account_override":
                estimate = pricing.estimate(model, input_tokens=usage.input, output_tokens=usage.output,
                                            cache_read=usage.cache_read, cache_write=usage.cache_write,
                                            provider=provider)
                usage = replace(usage, cost=estimate.cost)
        row = self.models.setdefault(model, {"usage": Usage(), "provider": provider, "seconds": 0.0})
        row["usage"].add(usage)
        row["provider"] = row["provider"] or provider
        row["seconds"] += seconds
        self.api_seconds += seconds

    def add_decision(self, decision: Any) -> None:
        self.decision_calls += 1
        self.decision_cost += float(getattr(decision, "cost", 0.0) or 0.0)
        self.decision_ms += int(getattr(decision, "latency_ms", 0) or 0)

    def count_diff(self, diff: str) -> None:
        for line in diff.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                self.lines_added += 1
            elif line.startswith("-"):
                self.lines_removed += 1

    def total(self) -> Usage:
        usage = Usage(requests=0)
        for row in self.models.values():
            usage.add(row["usage"])
        return usage

    def costs(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        rows = []
        api_equivalent = actual = 0.0
        unknown: List[str] = []
        for model, row in self.models.items():
            usage: Usage = row["usage"]
            estimate = pricing.estimate(model, input_tokens=usage.input, output_tokens=usage.output,
                                        cache_read=usage.cache_read, cache_write=usage.cache_write,
                                        provider=row.get("provider") or "", overrides=overrides)
            reported = usage.cost
            cost = reported if reported is not None else estimate.cost
            if estimate.price is None and reported is None and estimate.billing != "local":
                unknown.append(model)
            if estimate.price is not None and estimate.price.source == "account_override" and reported is not None:
                api_equivalent += cost
            else:
                api_equivalent += estimate.cost if estimate.price else (reported or 0.0)
            if estimate.billing == "api":
                actual += cost
            rows.append({"model": model, "usage": usage, "cost": cost, "estimate": estimate,
                         "reported": reported, "billing": estimate.billing, "seconds": row["seconds"]})
        rows.sort(key=lambda item: -item["cost"])
        return {"rows": rows, "api_equivalent": api_equivalent + self.decision_cost, "actual": actual + self.decision_cost,
                "decisions": self.decision_cost, "unknown": unknown}

class BackgroundJobs:
    """Parallel agents (``/background``, ``Task`` with run_in_background, the ``Agents`` tool).

    Round 32 — agents work like a team lead's helpers: each gets a task, a short **name** and a **role**, runs
    in its own isolated context, and the main agent can **send follow-up instructions** to a running agent
    (delivered at its next turn boundary — after the tool results of its current step) or to a finished one
    (it resumes with its context intact). ``wait`` returns when one / all of the chosen agents finish;
    ``cancel`` stops one. Reports come back as concise structured text.

    Jobs stay plain dicts (``id``, ``status``, ``prompt``, ``result``, ``log``, ``task`` …) — the interface
    and older tests read them directly."""

    MAX_RUNNING = 8

    def __init__(self, runtime: "Runtime") -> None:
        self.runtime = runtime
        self.jobs: List[Dict[str, Any]] = []

    def start(self, prompt: str, *, kind: str = "general-purpose", origin: str = "user",
              description: str = "", model: str = "", name: str = "", role: str = "",
              tools: Optional[List[str]] = None) -> Dict[str, Any]:
        """A parallel agent. ``origin`` is who asked: the user (/subtask) or the main model (Task with
        run_in_background) — only the model's own jobs wake it up when they finish. ``model`` is the one the
        asker wants for it; when several agent models are configured and none is asked for, one is chosen.
        ``name``/``role``/``tools`` (Round 32) label the agent, add a role to its instructions and narrow its
        tools."""
        from .agent import Agent, EphemeralSession
        if self.running() >= self.MAX_RUNNING:
            raise ValueError("Eight agents are already running. Wait for one or cancel it with /tasks.")
        agent_def = self.runtime.agent_spec(kind, role=role, tools=tools)
        number = len(self.jobs) + 1
        name = _agent_name(name, description or prompt, number, {job.get("name") for job in self.jobs})
        owner = self.runtime.session
        session = EphemeralSession(owner)
        job: Dict[str, Any] = {"id": number, "prompt": prompt, "status": "running", "started": time.time(),
                               "finished": 0.0, "log": [], "result": "", "tools": 0, "session_id": owner.id,
                               "origin": origin, "description": description or prompt.splitlines()[0][:80],
                               "last": "", "announced": False, "model": "", "name": name,
                               "role": (role or "").strip()[:200], "kind": kind, "inbox": [], "turns": 0,
                               "tokens": 0, "followups": 0, "done_event": asyncio.Event()}
        ui = _JobUI(self.runtime.ui, job)
        agent = Agent(self.runtime, session=session, ui=ui, depth=1, agent_def=agent_def, model=self.runtime.model)
        agent.inbox = job["inbox"]
        job["agent"] = agent
        self.jobs.append(job)
        job["task"] = asyncio.get_running_loop().create_task(self._work(job, prompt, model, first=True))
        return job

    async def _work(self, job: Dict[str, Any], prompt: str, model: str = "", *, first: bool = False) -> None:
        agent = job["agent"]
        number = job["id"]
        try:
            if first:
                chosen, why = await self.runtime.pick_worker_model(f"{job['description']}\n{prompt}",
                                                                   agent_def=agent.agent_def, requested=model)
                agent.model_override = chosen
                job["model"] = chosen
                if why:
                    job["last"] = f"{chosen} ({why})"
            result = await agent.run_turn(prompt, kind="prompt" if first else "command")
            job["turns"] += 1
            job["tokens"] += int(result.usage.input + result.usage.output)
            job["status"] = {"done": "done", "interrupted": "cancelled"}.get(result.stopped, result.stopped)
            job["result"] = result.text or result.error
            job["tools"] = getattr(agent.ui, "tools_used", job["tools"])
        except asyncio.CancelledError:
            job["status"] = "cancelled"
        except Exception as exc:
            job["status"] = "error"
            job["result"] = str(exc)
        job["finished"] = time.time()
        job["done_event"].set()
        origin = job["origin"]
        label = _job_label(job)
        if job["status"] == "done":
            self.runtime.add_note(
                f"[Background task #{number} finished]{label}\nTask: {job['prompt'][:2000]}\nReport:\n"
                f"{job['result'][:12000]}", wake=origin == "model", key=f"job:{number}")
        elif job["status"] in ("error", "cancelled") and origin == "model":
            self.runtime.add_note(f"[Background task #{number} {job['status']}]{label}\nTask: {job['prompt'][:600]}\n"
                                  f"{job['result'][:1500]}", wake=True, key=f"job:{number}")
        self.runtime.ui.background_finished(job)

    def find(self, ref: Any, session_id: str = "") -> Optional[Dict[str, Any]]:
        """A job by number (``3``, ``"#3"``) or name (case-insensitive), in this session when given."""
        text = str(ref if ref is not None else "").strip().lstrip("#").strip()
        if not text:
            return None
        rows = [job for job in self.jobs if not session_id or job.get("session_id") == session_id]
        if text.isdigit():
            return next((job for job in rows if job["id"] == int(text)), None)
        lowered = text.lower()
        return next((job for job in reversed(rows) if str(job.get("name") or "").lower() == lowered), None)

    def running(self) -> int:
        return sum(1 for job in self.jobs if job["status"] == "running")

    def send(self, ref: Any, message: str, *, session_id: str = "") -> Dict[str, Any]:
        """Follow-up instructions for an agent. Running: queued for its next turn boundary. Finished: the agent
        resumes with its own context and the message as the next request."""
        job = self.find(ref, session_id)
        if job is None:
            raise ValueError(f"No agent {ref!r} in this session")
        message = str(message or "").strip()
        if not message:
            raise ValueError("message is required")
        job["followups"] = int(job.get("followups") or 0) + 1
        if job["status"] == "running":
            job["inbox"].append(message[:20000])
            job["log"].append("» " + message.splitlines()[0][:160])
            return {"queued": True, "resumed": False, "job": job}
        if self.running() >= self.MAX_RUNNING:
            raise ValueError("Eight agents are already running. Wait for one first.")
        self.runtime.discard_note(f"job:{job['id']}")
        job["status"] = "running"
        job["finished"] = 0.0
        job["done_event"] = asyncio.Event()
        job["log"].append("» " + message.splitlines()[0][:160])
        job["task"] = asyncio.get_running_loop().create_task(
            self._work(job, "[Follow-up from the main agent]\n" + message[:20000]))
        return {"queued": False, "resumed": True, "job": job}

    def cancel(self, number: Any) -> bool:
        job = self.find(number)
        if job is not None and job["status"] == "running":
            job["task"].cancel()
            return True
        return False

    async def wait(self, refs: Optional[List[Any]] = None, *, mode: str = "all", timeout: float = 60.0,
                   session_id: str = "") -> List[Dict[str, Any]]:
        """Wait until one ("any") or every ("all") chosen agent is finished, at most ``timeout`` seconds.
        Without refs: every agent of this session that is running (or, when none is, the latest ones)."""
        if refs:
            jobs = []
            for ref in refs:
                job = self.find(ref, session_id)
                if job is None:
                    raise ValueError(f"No agent {ref!r} in this session")
                if job not in jobs:
                    jobs.append(job)
        else:
            jobs = [job for job in self.jobs if job["status"] == "running"
                    and (not session_id or job.get("session_id") == session_id)]
        waiting = [job for job in jobs if job["status"] == "running"]
        if waiting and timeout > 0:
            events = [asyncio.ensure_future(job["done_event"].wait()) for job in waiting]
            try:
                await asyncio.wait(events, timeout=timeout,
                                   return_when=asyncio.FIRST_COMPLETED if mode == "any" else asyncio.ALL_COMPLETED)
            finally:
                for event in events:
                    event.cancel()
        return jobs

    def summary(self, job: Dict[str, Any], *, report_chars: int = 6000) -> Dict[str, Any]:
        """A compact, structured view of one agent for the model."""
        end = job["finished"] or time.time()
        row: Dict[str, Any] = {"id": job["id"], "name": job.get("name") or "", "status": job["status"],
                               "seconds": int(end - job["started"]), "tools": job.get("tools", 0)}
        if job.get("role"):
            row["role"] = job["role"]
        if job.get("model"):
            row["model"] = job["model"]
        if job.get("tokens"):
            row["tokens"] = job["tokens"]
        if job["status"] == "running":
            row["doing"] = job.get("last") or ""
            if job.get("inbox"):
                row["queued_followups"] = len(job["inbox"])
        else:
            report = str(job.get("result") or "")
            if len(report) > report_chars:
                report = report[:report_chars] + f"\n…[{len(report) - report_chars} more characters: " \
                                                  f"TaskOutput(task_id={job['id']})]"
            row["report"] = report
        return row

def _agent_name(name: str, text: str, number: int, taken: set) -> str:
    """A short unique handle: the asker's name, else one from the task's first words."""
    import re as _re
    base = _re.sub(r"[^\w-]+", "-", str(name or "").strip().lower()).strip("-")[:24]
    if not base:
        words = _re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", str(text or ""))[:2]
        base = "-".join(word.lower() for word in words)[:24] or "agent"
    candidate, index = base, 2
    while candidate in taken or candidate.isdigit():
        candidate, index = f"{base}-{index}", index + 1
    return candidate

def _job_label(job: Dict[str, Any]) -> str:
    parts = [part for part in (job.get("name"), job.get("role")) if part]
    return f" ({' — '.join(parts)})" if parts else ""

class _JobUI(RelayUI):
    def __init__(self, parent: BaseUI, job: Dict[str, Any]) -> None:
        super().__init__(parent, None, None, label=f"#{job['id']}")
        self.job = job

    def _line(self, text: str) -> None:
        self.job["log"].append(text)
        del self.job["log"][:-200]
        self.job["last"] = text.strip()[:120]
        self.job["tools"] = self.tools_used

    async def ask_permission(self, request: Any) -> Dict[str, Any]:
        request.reason = (tr(f"[фоновая задача #{self.job['id']}] ", f"[background task #{self.job['id']}] ")
                          + (request.reason or ""))
        return await self.parent.ask_permission(request)

class Runtime:
    def __init__(self, root: Path | str, settings: Settings, *, ui: Optional[BaseUI] = None,
                 session: Optional[Session] = None, model: str = "") -> None:
        self.root = Path(root).resolve()
        self._prefs: Tuple[float, Dict[str, Any]] = (0.0, {})
        self.settings = settings
        self.ui: BaseUI = ui or BaseUI()
        self.session = session or Session(self.root)
        self.router = Router(settings)
        self.files = FileIndex(self.root)
        self.files.on_ready = self._index_ready
        self.tools = build_registry()
        self.permissions = PermissionManager(self)
        self.hooks = HookRunner(self)
        self.mcp = CliMCP(self)
        self.shells = BackgroundShells()
        self.stats = Stats()
        self.jobs = BackgroundJobs(self)

        self.decision_transport: Any = None
        self.decisions = DecisionClient(self.decision_config(), llm=self._decisions_llm)

        self.turn_hints: List[str] = []
        self.add_dirs: List[Path] = []
        for folder in settings.get("permissions.additional_directories") or []:
            path = Path(os.path.expanduser(str(folder)))
            if path.is_dir():
                self.add_dirs.append(path.resolve())

        self.model = model or str(settings.get("model") or self.session.meta.get("model")
                                  or self.memuai_prefs().get("default_model") or "")
        self.browser: Any = None
        self.computer: Any = None
        self.skills = project.Skills(self.root, settings.is_trusted(self.root), settings)
        self._prompt_cache: Dict[Tuple[Any, ...], str] = {}

        self.context_measured: Optional[Dict[str, Any]] = None
        self._git_cache: Dict[str, str] = {}
        self._memory_cache: Optional[List[project.MemoryFile]] = None
        self._memory_key: Optional[tuple] = None
        self._agents_cache: Optional[Dict[str, project.AgentDef]] = None
        self._commands_cache = None
        self.session_context: List[str] = []
        self.goal_continuations = 0
        self._ready: Optional[asyncio.Event] = None
        self._housekeeping_timer: Optional[threading.Timer] = None
        self.mcp_task: Optional[asyncio.Task] = None
        self.notes: List[Dict[str, Any]] = []
        self.last_error = ""
        if self.model and not self.session.meta.get("model"):
            self.session.meta["model"] = self.model

    def memuai_prefs(self) -> Dict[str, Any]:
        stamp, data = self._prefs
        if time.time() - stamp < 30:
            return data
        try:
            data = json.loads((paths.memu_dir() / "preferences.json").read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        self._prefs = (time.time(), data)
        return data

    def trusted(self) -> bool:
        return self.settings.is_trusted(self.root)

    def memory_files(self) -> List[project.MemoryFile]:
        trusted = self.trusted()
        names = list(self.settings.get("memory_files") or ["MEMUCODE.md", "AGENTS.md", "CLAUDE.md"])
        key = (str(self.root), trusted, tuple(names))
        if self._memory_cache is None or self._memory_key != key:
            self._memory_cache = project.memory_files(self.root, names, trusted=trusted)
            self._memory_key = key
        return self._memory_cache

    def agents(self) -> Dict[str, project.AgentDef]:
        if self._agents_cache is None:
            self._agents_cache = project.load_agents(self.root, self.trusted())
        return self._agents_cache

    def commands(self) -> Dict[str, project.CustomCommand]:
        if self._commands_cache is None:
            self._commands_cache = project.load_commands(self.root, self.trusted())
        return self._commands_cache

    def git_snapshot(self) -> str:
        """``git status``/``log`` of the project as of the session start (cached: it spawns processes)."""
        key = str(self.root)
        cached = self._git_cache.get(key)
        if cached is None:
            from .prompts import git_snapshot
            cached = self._git_cache[key] = git_snapshot(self.root)
        return cached

    def _index_ready(self) -> None:
        """The file index finished a scan (from its thread): let the interface refresh its ``@`` list."""
        callback = getattr(self.ui, "files_ready", None)
        if callback is not None:
            callback()

    def prewarm(self) -> None:
        """Fill the caches the first keystroke or the first message would otherwise build on the
        interface thread: the file list, git state, custom commands, skills, memory files, agents."""
        for step in (self.files.files, self.git_snapshot, self.commands, self.skills.catalogue,
                     self.memory_files, self.agents):
            try:
                step()
            except Exception:
                continue

    def invalidate_prompt(self, *, memory: bool = False) -> None:
        self._prompt_cache.clear()
        self.context_measured = None
        if memory:
            self._commands_cache = None
            self._memory_cache = None
            self._agents_cache = None
            self.skills = project.Skills(self.root, self.trusted(), self.settings)

    def system_prompt(self, tools: List[Any], *, native: bool, agent: Optional[project.AgentDef] = None) -> str:
        from .prompts import build_system_prompt
        key = (str(self.root), self.trusted(), tuple(self.settings.get("memory_files") or []),
               native, (agent.name, agent.prompt, tuple(agent.tools or ())) if agent else "",
               tuple(tool.name for tool in tools), self.model,
               str(self.settings.get("output_style")), str(self.settings.get("subagents")),
               tuple(str(path) for path in self.add_dirs), tuple(worker_models(self.settings)),
               str(self.settings.get("worker_pick")), repr(self.settings.get("model_notes")),
               repr(self.settings.get("model_routing")), self._decisions_fingerprint(),
               self._vision_fingerprint())
        cached = self._prompt_cache.get(key)
        if cached is None:
            cached = build_system_prompt(self, tools, native=native, agent=agent)
            self._prompt_cache[key] = cached
        return cached

    def _vision_fingerprint(self) -> Tuple[Any, ...]:
        """Whether the model sees pictures and which model describes them: the system prompt says so."""
        from . import vision
        return (vision.support(self, self.model).can, vision.mode(self), vision.configured_model(self))

    def _decisions_fingerprint(self) -> str:
        """The parts of the decision model setup that the system prompt mentions (never the key)."""
        client = self.decisions
        return repr((client.provider, client.enabled, client.model, sorted(client.use_models.items()), client.fallbacks,
                     sorted((self.settings.get("decisions.uses") or {}).items())))

    def effective_window(self, model: str = "") -> int:
        model = model or self.model
        window, source = self.router.context_window(model)
        info = self.router.info(model)
        if (source not in ("settings", "memuai") and info is not None and info.backend == "proxy"
                and info.provider in SUBSCRIPTION_PROVIDERS):
            window = min(window, PROXY_WINDOW_CAP)
        return max(8_000, window)

    def small_model(self) -> str:
        fast = routing_config(self.settings)["tiers"]["fast"]
        return str(self.settings.get("small_model") or (fast[0] if fast else "") or self.model)

    def small_model_available(self) -> bool:
        return bool(self.small_model())

    def set_model(self, model: str) -> None:
        self.model = model
        self.session.set_meta(model=model)
        self.invalidate_prompt()

    async def quick_answer(self, system: str, user: str, *, model: str = "", max_tokens: int = 0) -> str:
        model = model or self.small_model()
        backend, raw = self.router.resolve(model)
        request = ChatRequest(model=raw, system=system, messages=[{"role": "user", "content": user}],
                              max_tokens=max_tokens, effort="low", session=self.session.id)
        started = time.monotonic()
        text, usage = await backend.complete(request)
        if not usage.input and not usage.output:
            usage = Usage(input=estimate_text(system) + estimate_text(user), output=estimate_text(text), requests=1)
        info = self.router.info(model)
        self.stats.add_usage(model, usage, provider=info.provider if info else "", seconds=time.monotonic() - started)
        return text

    def completion_fn(self) -> Callable[[str, List[Dict[str, Any]]], Awaitable[str]]:
        """``completion(model, messages) -> text`` for the browser/computer agents."""
        async def completion(model: str, messages: List[Dict[str, Any]]) -> str:
            system = ""
            rest: List[Dict[str, Any]] = []
            for message in messages:
                if message.get("role") == "system" and not system:
                    system = str(message.get("content") or "")
                else:
                    rest.append(message)
            backend, raw = self.router.resolve(model)
            started = time.monotonic()
            text, usage = await backend.complete(ChatRequest(model=raw, system=system, messages=rest,
                                                             session=self.session.id))
            info = self.router.info(model)
            if not usage.input and not usage.output:
                usage = Usage(input=estimate_text(json.dumps(rest)[:20000]) + 1500, output=estimate_text(text),
                              requests=1)
            self.stats.add_usage(model, usage, provider=info.provider if info else "",
                                 seconds=time.monotonic() - started)
            return text
        return completion

    async def route_model(self, prompt: str, looked_at: Any = None) -> Optional[Tuple[str, str]]:
        """``/model auto``: pick the model for one request from the tiers the user set up.

        The request is scored for difficulty (the decision model when one is connected, otherwise a
        keyword heuristic) which selects a tier — fast, balanced or strong. A tier with several models is
        decided again, from the user's remarks about each model: by the decision model, or by matching the
        remarks against the request. Returns (model, why), or None when routing is off or has nothing to
        choose between. ``looked_at`` is the result of :meth:`triage` when the caller already has it."""
        config = routing_config(self.settings)
        tiers = config["tiers"]
        if not config["enabled"] or not any(tiers.values()):
            return None
        notes = model_notes(self.settings)
        complexity: Optional[float] = None
        why = ""
        decisions = self.decisions
        if looked_at is None:
            try:
                looked_at = await self.triage(prompt, routing=True)
            except Exception:
                looked_at = None
        elif looked_at is False:
            looked_at = None
        if looked_at is not None and looked_at.complexity is not None:
            complexity = float(looked_at.complexity)
            why = f"{decisions.model_for('routing') or decisions.title} {complexity:.2f}"
        if complexity is None:
            heavy = re.search(r"(?i)refactor|implement|architect|migrat|debug|investigat|design|rewrite|"
                              r"реализ|рефактор|перепиш|архитект|отлад|исслед|миграц|спроектир|почин|исправ", prompt)
            complexity = 0.7 if heavy or len(prompt) > 500 else 0.2
            why = tr("эвристика", "heuristic")
        wanted = ("strong" if complexity >= config["strong_from"] else
                  "balanced" if complexity >= config["balanced_from"] else "fast")

        order = {"fast": ("fast", "balanced", "strong"), "balanced": ("balanced", "strong", "fast"),
                 "strong": ("strong", "balanced", "fast")}[wanted]
        tier = next((name for name in order if tiers[name]), "")
        if not tier:
            return None
        candidates = tiers[tier]
        chosen = candidates[0]
        if len(candidates) > 1:
            chosen, how = await self.choose_between(prompt, candidates, notes, use="routing")
            if how:
                why = f"{why}, {how}"
        return chosen, (f"{tier}: {why}" if why else tier)

    async def choose_between(self, task: str, candidates: List[str], notes: Dict[str, str], *, use: str
                             ) -> Tuple[str, str]:
        """(model, why) — which of several models suits the task, judged from the user's remarks about them.

        The decision model answers a *choice* question whose options are the models with their remarks; with
        none connected (or when it fails) the remarks are matched against the task by words."""
        candidates = [model for model in candidates if model]
        if len(candidates) <= 1:
            return (candidates[0] if candidates else ""), ""
        uses = self.settings.get("decisions.uses") or {}
        decisions = self.decisions
        if decisions.enabled and decisions.provider != "model" and uses.get(use, True):
            try:
                from ..decisions import pick_model_for
                answer = await asyncio.wait_for(pick_model_for(decisions.for_use(use), task, describe_candidates(
                    candidates, notes)), 6)
                self.stats.decision_calls += 1
                if answer in candidates:
                    return answer, decisions.model_for(use) or decisions.title
            except Exception:
                pass
        model, why = pick_by_notes(task, candidates, notes, extra={model: model for model in candidates})
        return model, why

    async def pick_worker_model(self, task: str, *, agent_def: Any = None, requested: str = "") -> Tuple[str, str]:
        """(model, why) for one agent. Never raises: an agent always gets a model."""
        try:
            models = worker_models(self.settings)
            if not models:
                return self.model, ""
            if requested:
                wanted = requested.strip()
                if wanted in models:
                    return wanted, tr("выбрала главная модель", "chosen by the main model")
                from .tui.input import match_models
                found = [item for _score, item in match_models(wanted, models, limit=3)]
                if len(found) == 1:
                    return found[0], tr("выбрала главная модель", "chosen by the main model")
            if len(models) == 1 or str(self.settings.get("worker_pick") or "model") == "first":
                return models[0], ""
            label = (f"{getattr(agent_def, 'name', '')}: {getattr(agent_def, 'description', '')}\n"
                     if agent_def is not None else "")
            return await self.choose_between(label + task, models, model_notes(self.settings), use="agents")
        except Exception:
            return self.model, ""

    async def _decisions_llm(self, system: str, user: str) -> str:
        return await self.quick_answer(system, user)

    def decision_config(self) -> Dict[str, Any]:
        """The ``decisions`` settings, with one convenience: an OpenRouter decision model that has no key of its
        own uses the key of the OpenRouter connection the user already added in ``/proxy`` (or the
        ``OPENROUTER_API_KEY`` of the environment, which the client reads itself)."""
        config = dict(self.settings.get("decisions") or {})
        if str(config.get("provider") or "") == "openrouter" and not str(config.get("api_key") or "").strip():
            for row in self.settings.connections():
                if "openrouter.ai" in str(row.get("base_url") or "") and str(row.get("api_key") or "").strip():
                    config["api_key"] = str(row["api_key"]).strip()
                    break
        return config

    def decision_key_source(self) -> str:
        """Where the decision model's key comes from, for the status line: own / connection / environment / none."""
        config = dict(self.settings.get("decisions") or {})
        if str(config.get("api_key") or "").strip():
            return "own"
        if str(config.get("provider") or "") == "openrouter":
            if any("openrouter.ai" in str(row.get("base_url") or "") and row.get("api_key") and not row.get("from_env")
                   for row in self.settings.connections()):
                return "connection"
        return "environment" if self.decisions.api_key else "none"

    def reload_decisions(self) -> None:
        self.decisions = DecisionClient(self.decision_config(), llm=self._decisions_llm,
                                        transport=self.decision_transport)

    async def triage(self, prompt: str, *, routing: bool = False, skills: bool = False, intent: bool = False) -> Any:
        """The decision model's view of a request (see :mod:`app.cli.triage`); None when there is none."""
        from .triage import triage
        return await triage(self, prompt, routing=routing, skills=skills, intent=intent)

    async def consult_advisor(self, question: str, context: str = "") -> str:
        model = str(self.settings.get("advisor_model") or "")
        if not model:
            raise LLMError(tr("Советник не выбран: /advisor <модель>", "No advisor model: /advisor <model>"))
        system = ("You are a principal engineer advising a coding agent that works in the user's repository. "
                  "Give a decisive, concrete recommendation: the approach, why, the main risks and how to verify. "
                  "Point out anything in the agent's reasoning that looks wrong. Be brief and specific; no code "
                  "unless a short snippet is essential.")
        user = (f"<conversation_excerpt>\n{self.session.brief_context(14000)}\n</conversation_excerpt>\n"
                + (f"<details>\n{context[:20000]}\n</details>\n" if context else "")
                + f"Question from the agent: {question}")
        return await self.quick_answer(system, user, model=model)

    def agent_spec(self, kind: str = "general-purpose", *, role: str = "",
                   tools: Optional[List[str]] = None) -> project.AgentDef:
        """The definition an agent runs with: a type (built-in or the user's), plus — Round 32 — a role the
        asker gives it in its own words and a narrower tool list. Raises ValueError for an unknown type."""
        import dataclasses
        agents = self.agents()
        kind = str(kind or "general-purpose")
        agent_def = agents.get(kind) or agents.get(kind.lower())
        if agent_def is None:
            raise ValueError(f"Unknown agent type: {kind}. Available: {', '.join(agents)}")
        role = str(role or "").strip()[:600]
        wanted = [str(name).strip() for name in (tools or []) if str(name).strip()]
        if not role and not wanted:
            return agent_def
        prompt = agent_def.prompt + (f"\n\nYour role in this team: {role}" if role else "")
        allowed = agent_def.tools
        if wanted:
            known = {tool.name.lower(): tool.name for tool in self.tools.tools.values()} \
                if hasattr(self.tools, "tools") else {}
            names = [known.get(name.lower(), name) for name in wanted]
            allowed = [name for name in names if allowed is None or name in allowed] or allowed
        return dataclasses.replace(agent_def, prompt=prompt, tools=allowed)

    async def run_subagent(self, kind: str, description: str, prompt: str, ctx: Any,
                           requested_model: str = "", *, role: str = "",
                           tools: Optional[List[str]] = None) -> Dict[str, Any]:
        from .agent import Agent, EphemeralSession
        agents = self.agents()
        try:
            agent_def = self.agent_spec(kind, role=role, tools=tools)
        except ValueError:
            return {"ok": False, "error": f"Unknown subagent_type {kind!r}. Available: {', '.join(agents)}",
                    "report": ""}
        model, _why = await self.pick_worker_model(f"{description}\n{prompt}", agent_def=agent_def,
                                                   requested=requested_model)
        session = EphemeralSession(self.session)
        relay = RelayUI(ctx.interface, None, ctx.call, label=agent_def.name)
        agent = Agent(self, session=session, ui=relay, agent_def=agent_def, depth=ctx.depth + 1, model=model)
        started = time.monotonic()
        result = await agent.run_turn(prompt)
        seconds = time.monotonic() - started
        await self.hooks.run("SubagentStop", {"subagent_type": agent_def.name, "description": description})
        tokens = result.usage.input + result.usage.output
        summary = tr(f"{relay.tools_used} инстр. · {tokens:,} ток. · {seconds:.0f} с",
                     f"{relay.tools_used} tools · {tokens:,} tokens · {seconds:.0f}s")
        report = result.text or result.error or "(the sub-agent returned no report)"
        return {"ok": result.stopped == "done", "report": report, "summary": summary,
                "usage": result.usage.to_dict(), "error": result.error}

    async def goal_followup(self, result: Any) -> Optional[str]:
        """After a turn: the next continuation prompt while a goal is active, else None."""
        goal = self.session.meta.get("goal")
        if not isinstance(goal, dict) or not goal.get("text") or goal.get("done") or goal.get("paused"):
            return None
        if getattr(result, "stopped", "") == "stuck":
            self._set_goal(paused=True)
            return None
        if getattr(result, "stopped", "") not in ("done",):
            return None
        text = str(getattr(result, "text", "") or "")
        if "GOAL COMPLETE" in text.upper():
            self._set_goal(done=True)
            return None
        if "GOAL BLOCKED" in text.upper():
            self._set_goal(paused=True)
            return None
        uses = self.settings.get("decisions.uses") or {}
        if self.decisions.enabled and uses.get("goal", True):
            try:
                from ..decisions import goal_reached
                verdict = await asyncio.wait_for(goal_reached(self.decisions.for_use("goal"), goal=goal["text"],
                                                              report=text), 10)
                self.stats.decision_calls += 1
                if verdict["done"] >= 0.85:
                    self._set_goal(done=True)
                    return None
                if verdict["blocked"] >= 0.75:
                    self._set_goal(paused=True)
                    return None
            except Exception:
                pass
        limit = int(goal.get("max") or 10)
        used = int(goal.get("continuations") or 0)
        if used >= limit:
            self._set_goal(paused=True)
            self.ui.notice(tr(f"Цель: достигнут предел продолжений ({limit}). /goal continue — ещё.",
                              f"Goal: continuation limit reached ({limit}). /goal continue for more."), "warn")
            return None
        goal["continuations"] = used + 1
        self.session.set_meta(goal=goal)
        return ("Continue working toward the goal. Take the next verified step. If the goal is fully achieved, "
                "prove it and reply with `GOAL COMPLETE: <evidence>`; if you cannot continue without me, reply "
                "with `GOAL BLOCKED: <reason>`.")

    def _set_goal(self, **values: Any) -> None:
        goal = dict(self.session.meta.get("goal") or {})
        goal.update(values)
        self.session.set_meta(goal=goal)
        if values.get("done"):
            self.ui.notice(tr("◎ Цель достигнута", "◎ Goal achieved"), "success")
        elif values.get("paused"):
            self.ui.notice(tr("◎ Цель на паузе: нужна ваша помощь", "◎ Goal paused: your input is needed"), "warn")

    def record_round(self, agent: Any, output: Any) -> None:
        if not self.settings.get("stats.record", True):
            return
        usage = output.usage
        first = bool(getattr(agent, "first_round", False)) and agent.depth == 0
        agent.first_round = False
        event = {"day": _dt.date.today().isoformat(), "hour": _dt.datetime.now().hour,
                 "session": "cli-" + self.session.id, "model": agent.model,
                 "tokens_in": usage.input, "tokens_out": usage.output, "estimated": usage.estimated,
                 "user_message": first, "source": "cli"}

        def work() -> None:
            try:
                from .. import code_stats
                code_stats.record(event)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def log_error(self, text: str) -> None:
        self.last_error = text[-4000:]
        try:
            path = paths.home() / "errors.log"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text.rstrip() + "\n")
        except OSError:
            pass

    def add_dir(self, folder: Path | str) -> Path:
        path = Path(os.path.expanduser(str(folder))).resolve()
        if not path.is_dir():
            raise ValueError(tr(f"Папка не найдена: {path}", f"Folder not found: {path}"))
        if path not in self.add_dirs and path != self.root:
            self.add_dirs.append(path)
            self.invalidate_prompt()
        return path

    def change_root(self, folder: Path | str) -> Path:
        path = Path(os.path.expanduser(str(folder)))
        if not path.is_absolute():
            path = self.root / path
        path = path.resolve()
        if not path.is_dir():
            raise ValueError(tr(f"Папка не найдена: {path}", f"Folder not found: {path}"))
        self.root = path
        os.chdir(path)
        self.settings.root = path
        self.settings.reload()
        self.files = FileIndex(path)
        self.files.on_ready = self._index_ready
        self.session = Session(path)
        self.session.meta["model"] = self.model
        self.add_dirs = [folder for folder in self.add_dirs if folder != path]
        self.invalidate_prompt(memory=True)
        return path

    def new_session(self) -> Session:
        self._git_cache.clear()
        self.session = Session(self.root)
        self.session.meta["model"] = self.model
        self.goal_continuations = 0
        self.invalidate_prompt()
        return self.session

    def add_note(self, text: str, *, wake: bool = False, key: str = "") -> None:
        """A finished background agent or command: the model gets it before its next step; with
        ``wake`` an idle interface also starts a turn so the model can act on it."""
        self.notes.append({"text": text, "wake": wake, "key": key})

    def discard_note(self, key: str) -> None:
        self.notes = [note for note in self.notes if not (key and note.get("key") == key)]

    def drain_notes(self) -> List[str]:
        rows, self.notes = self.notes, []
        return [str(row["text"]) for row in rows]

    def wake_requested(self) -> bool:
        return any(row.get("wake") for row in self.notes)

    def poll_background(self) -> List[str]:
        """Background commands that ended since the last look: queue a note for the model, return UI lines."""
        lines: List[str] = []
        for row in list(self.shells.rows.values()):
            if row.get("announced") or not row["output_done"].is_set():
                continue
            row["announced"] = True
            code = row.get("exit_code")
            with self.shells.lock:
                tail = row["buffer"][-3000:]
            self.add_note(f"[Background command {row['id']} finished with exit code {code}]\n"
                          f"Command: {row['command']}\n--- output tail ---\n{tail or '(no output)'}",
                          wake=True, key="shell:" + row["id"])
            lines.append(f"Background command {row['id']} finished (exit {code}): {row['command'][:70]}")
        return lines

    async def wait_ready(self, timeout: float = 30.0) -> bool:
        """Wait until :meth:`startup` (models, hooks) has finished; True at once when it never started."""
        event = self._ready
        if event is None or event.is_set():
            return True
        try:
            await asyncio.wait_for(event.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return True

    async def startup(self, *, load_models: bool = True) -> None:
        self._ready = asyncio.Event()
        try:

            self._housekeeping_timer = threading.Timer(4.0, self._housekeeping)
            self._housekeeping_timer.daemon = True
            self._housekeeping_timer.start()
            if load_models:
                try:
                    models = await asyncio.wait_for(self.router.models(), 25)
                except Exception as exc:
                    models = []
                    self.router.errors["proxy"] = str(exc)
                self.pick_default_model(models)
            self.mcp_task = asyncio.get_running_loop().create_task(self._start_mcp())
            from .tools import connected
            await connected.refresh(self)
            outcome = await self.hooks.run("SessionStart", {"source": "resume" if self.session.loaded else "startup"})
            self.session_context.extend(outcome.context)
        finally:
            self._ready.set()

    def pick_default_model(self, models: List[ModelInfo]) -> None:
        ids = [row.id for row in models if not row.image]
        if self.model and (self.model in ids or self.model.startswith("@") or not ids):
            return
        prefs = self.memuai_prefs()
        candidates = [str(prefs.get("default_model") or "")]
        pinned = prefs.get("pinned_models") or []
        if isinstance(pinned, list):
            candidates += [str(item) for item in pinned]
        for candidate in candidates:
            if candidate and candidate in ids:
                self.model = candidate
                break
        else:
            if ids and not self.model:
                self.model = ids[0]
        self.session.meta["model"] = self.model

    async def _start_mcp(self) -> None:
        try:
            await self.mcp.start_all()
            if self.mcp.servers:
                self.invalidate_prompt()
        except Exception as exc:
            self.log_error(f"mcp: {exc}")

    def _housekeeping(self) -> None:
        try:
            from .session import cleanup

            cleanup(int(self.settings.get("cleanup_period_days") or 45), keep={self.session.id})
        except Exception:
            pass
        if self.settings.get("pricing.online_catalog", True):
            try:
                pricing.refresh_online()
            except Exception:
                pass

    async def shutdown(self) -> None:

        if self._housekeeping_timer is not None:
            self._housekeeping_timer.cancel()
        try:
            await asyncio.wait_for(self.hooks.run("SessionEnd", {"reason": "exit"}), 10)
        except Exception:
            pass
        for job in self.jobs.jobs:
            if job["status"] == "running":
                job["task"].cancel()
        if self.jobs.jobs:
            await asyncio.gather(*(job["task"] for job in self.jobs.jobs), return_exceptions=True)
        if self.mcp_task is not None and not self.mcp_task.done():
            self.mcp_task.cancel()
            await asyncio.gather(self.mcp_task, return_exceptions=True)
        try:
            await asyncio.wait_for(self.mcp.stop_all(), 10)
        except Exception:
            pass
        self.shells.kill_all()
        for automation in (self.browser, self.computer):
            if automation is not None:
                try:
                    await asyncio.wait_for(automation.stop(), 5)
                except Exception:
                    pass
        try:
            from .. import code_sandbox
            await asyncio.to_thread(code_sandbox.manager.close, self.session.id)
        except Exception:
            pass
        try:
            self.session.flush_index()
        except Exception:
            pass
        await self.router.close()
