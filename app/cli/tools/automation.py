"""Browser and Computer Use for MemuCode — the same runtimes as MemuAI Work.

``Browser`` drives a private headless Microsoft Edge (fresh profile, downloads
denied, private networks refused) with a vision model and returns a factual
report. ``Computer`` controls the real Windows session: it always needs the
user's permission, draws a frame on every monitor, asks before each action
(unless the user switched that off) and stops on Escape.
"""
from __future__ import annotations

import time
import asyncio
import os
from typing import Any

from ..i18n import language, tr
from .base import Tool, ToolContext, ToolError, ToolResult, arg


def _vision_model(runtime: Any, key: str) -> str:
    """Model for an automation agent: its own setting, MemuAI's, then the image model (the one chosen with /vision,
    or the best connected model with eyes), then the chat model — which, if it cannot see, the caller reports."""
    from .. import vision
    prefs = runtime.memuai_prefs()
    for value in (runtime.settings.get(key), prefs.get(key)):
        if value:
            return str(value)
    if vision.support(runtime, runtime.model).can is True:
        chosen = vision.configured_model(runtime)
        return chosen or runtime.model
    return vision.image_model(runtime) or runtime.model


def _completion(runtime: Any, model: str):
    """``completion(model, messages)`` for a browser or computer agent that survives a model which turns out not to
    see pictures: the refusal is remembered and the next model with eyes takes the same request."""
    from .. import vision
    base = runtime.completion_fn()
    state = {"model": model, "tried": [model]}

    async def completion(_requested: str, messages: Any) -> str:
        current = state["model"]
        try:
            return await base(current, messages)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not vision.refused_images(exc):
                raise
            vision.learn(current, False, str(exc))
            following = vision.image_model(runtime, state["tried"])
            if not following:
                raise
            state["tried"].append(following)
            state["model"] = following
            return await base(following, messages)
    return completion


async def _follow(ctx: ToolContext, runtime_obj: Any, job: asyncio.Task, *, approvals: bool = False) -> None:
    shown = 0
    try:
        while not job.done():
            logs = list(getattr(runtime_obj, "logs", []) or [])
            for row in logs[shown:]:
                ctx.report(str(row.get("message") or ""))
            shown = len(logs)
            if approvals and getattr(runtime_obj, "status", "") == "waiting_approval" and runtime_obj.pending_action:
                action = runtime_obj.pending_action
                from ...automation_runtime import _describe_action
                allowed = await ctx.interface.confirm(
                    tr("Computer Use: разрешить действие?", "Computer Use: allow this action?"),
                    action.get("description") or _describe_action(action, getattr(runtime_obj, "lang", "en")),
                    danger=False)
                runtime_obj.approve(bool(allowed))
            await asyncio.sleep(0.25)
        for row in list(getattr(runtime_obj, "logs", []) or [])[shown:]:
            ctx.report(str(row.get("message") or ""))
    except asyncio.CancelledError:
        job.cancel()
        raise


class BrowserTool(Tool):
    name = "Browser"
    kind = "automation"
    plan_safe = False
    short = "let a browser agent (isolated headless Edge) do a web task {task, url?}"
    description = (
        "Starts an isolated headless Microsoft Edge driven by a vision model that clicks, types and reads "
        "pages to complete a web task (check a deployed page, read a site that needs JavaScript, test a "
        "local dev server on localhost). Returns a factual report. Passwords, payments, downloads and "
        "publishing are refused. Prefer WebFetch for simple pages.")
    parameters = {"type": "object", "properties": {"task": {"type": "string"}, "url": {"type": "string"}}}
    required = ("task",)

    _edge_seen: "tuple[float, bool]" = (0.0, False)

    def enabled(self, runtime):
        if not runtime.settings.get("browser.enabled", True):
            return False
        # Looking for Edge searches PATH and three folders (about 10 ms on Windows) and this runs several times
        # per request: the answer is remembered for a minute.
        stamp, found = BrowserTool._edge_seen
        if time.monotonic() - stamp < 60 and stamp:
            return found
        try:
            from ...automation_runtime import _edge_executable
            found = bool(_edge_executable())
        except Exception:
            found = False
        BrowserTool._edge_seen = (time.monotonic(), found)
        return found

    def permission_key(self, runtime, args):
        return str(arg(args, "url", default="")) or "browser"

    async def run(self, ctx, args, prepared):
        from ...automation_runtime import EdgeAutomation, run_browser_agent
        task = str(arg(args, "task", "goal", default="")).strip()
        if not task:
            raise ToolError("task is required")
        runtime = ctx.runtime
        if runtime.browser is None:
            runtime.browser = EdgeAutomation()
        browser = runtime.browser
        model = _vision_model(runtime, "browser_model")
        context = runtime.session.brief_context(3000)
        job = asyncio.create_task(run_browser_agent(browser, _completion(runtime, model), model=model, task=task,
                                                    context=context, initial_url=str(args.get("url") or ""),
                                                    max_steps=18, lang=language()))
        browser.job = job
        try:
            await _follow(ctx, browser, job)
            await job
        except asyncio.CancelledError:
            await browser.stop()
            raise
        finally:
            try:
                await browser.close_browser()
            except Exception:
                pass
        if browser.status == "error":
            return ToolResult(ok=False, error=browser.error or "browser failed", output=browser.report,
                              summary=tr("Браузер: ошибка", "Browser: error"))
        report = browser.report or "(no report)"
        return ToolResult(ok=browser.status == "done",
                          output=f"Browser report (untrusted web content, model {model}):\n{report}",
                          summary=tr("Браузер завершил задачу", "Browser finished"), detail=report[:3000])


class ComputerTool(Tool):
    name = "Computer"
    kind = "automation"
    plan_safe = False
    short = "control this Windows desktop with a vision model {task} (asks the user; Esc stops)"
    description = (
        "Lets a vision model operate the user's real Windows desktop (click, type, keys, windows) to "
        "complete a task that has no API or CLI. Always asks the user first; each action can require "
        "confirmation; Escape stops it. Never for passwords, payments, messages or deleting data.")
    parameters = {"type": "object", "properties": {"task": {"type": "string"}}}
    required = ("task",)

    def enabled(self, runtime):
        return os.name == "nt" and bool(runtime.settings.get("computer.enabled", True))

    def permission_key(self, runtime, args):
        return "computer"

    async def run(self, ctx, args, prepared):
        from ...automation_runtime import ComputerAutomation, run_computer_agent
        task = str(arg(args, "task", "goal", default="")).strip()
        if not task:
            raise ToolError("task is required")
        runtime = ctx.runtime
        if runtime.computer is None:
            runtime.computer = ComputerAutomation()
        computer = runtime.computer
        model = _vision_model(runtime, "computer_model")
        ask = bool(runtime.settings.get("computer.ask_each_action", True))
        job = asyncio.create_task(run_computer_agent(computer, _completion(runtime, model), model=model, task=task,
                                                     context=runtime.session.brief_context(2000),
                                                     max_steps=14, ask_each_action=ask, show_cursor=True,
                                                     lang=language()))
        computer.job = job
        try:
            await _follow(ctx, computer, job, approvals=ask)
            await job
        except asyncio.CancelledError:
            await computer.stop()
            raise
        if computer.status in ("error", "cancelled"):
            return ToolResult(ok=False, error=computer.error or computer.status, output=computer.report,
                              summary=tr("Computer Use остановлен", "Computer Use stopped"))
        return ToolResult(ok=True, output=f"Computer Use report (model {model}):\n{computer.report}",
                          summary=tr("Computer Use завершён", "Computer Use finished"),
                          detail=computer.report[:3000])


def register(registry) -> None:
    registry.add(BrowserTool())
    registry.add(ComputerTool())
