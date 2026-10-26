# -*- coding: utf-8 -*-
"""Round 35: Quick Access scenarios (own commands) and quick-action macros as flows.

One automation model: a scenario (``assistant.json`` → ``commands``) and a quick-action button
(``quick_canvas.json`` → ``actions``) are shown in «Планирование» in the same step format as a task flow
(:mod:`app.flow_engine`). Their own runners stay the only executors (zero regressions): the adapters only

* describe them as flows (``scenario_to_flow`` / ``macro_to_flow``; every step keeps its original as
  ``origin``, so ``flow_to_scenario_steps`` / ``flow_to_macro_steps`` round-trip losslessly);
* run them as one step of a task flow (``qa_command`` / ``qa_macro`` / ``tool``) through the window-process
  handoff (:func:`run_handoff`, called by ``schedule_rules_api.Executor``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("memuai.flows.adapters")

def tr(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru

def _sid(prefix: str, index: int) -> str:
    return f"{prefix}{index + 1}"

def scenario_step_to_flow(step: Dict[str, Any], index: int, lang: str = "ru") -> Dict[str, Any]:
    kind = str(step.get("type") or "action")
    out: Dict[str, Any] = {"id": _sid("q", index), "origin": dict(step)}
    if kind == "action":
        out.update(type="tool", action=step.get("action") or "", params=dict(step.get("params") or {}))
        try:
            from . import assistant_catalog as catalog
            meta = catalog.BY_ID.get(step.get("action") or "") or {}
            title = meta.get("title") or {}
            out["label"] = (title.get("en") if lang == "en" else title.get("ru")) or step.get("action") or ""
        except Exception:
            pass
    elif kind == "wait":
        out.update(type="wait", seconds=float(step.get("seconds") or 1))
    elif kind == "ask_ai":
        out.update(type="model", prompt=step.get("prompt") or "")
    elif kind in ("say", "notify"):
        out.update(type="notify", text=step.get("text") or "", title="",
                   label=tr(lang, "Сказать вслух", "Say aloud") if kind == "say" else "")
    elif kind == "layout":
        out.update(type="tool", action="layout", label=tr(lang, "Раскладка окон", "Window layout"))
    else:
        out.update(type="tool", action=kind)
    if step.get("continue_on_error"):
        out["continue_on_error"] = True
    if step.get("when"):
        out["when"] = step["when"]
    return out

def scenario_to_flow(command: Dict[str, Any], lang: str = "ru") -> Dict[str, Any]:
    steps = [scenario_step_to_flow(s, i, lang) for i, s in enumerate(command.get("steps") or []) if isinstance(s, dict)]
    return {"source": "scenario", "id": str(command.get("id") or ""), "name": str(command.get("name") or ""),
            "enabled": command.get("enabled", True) is not False, "steps": steps,
            "starts": {"phrases": list(command.get("triggers") or [])[:8], "hotkey": command.get("hotkey") or ""},
            "editable_in": "quick_commands"}

def macro_step_to_flow(step: Dict[str, Any], index: int, lang: str = "ru") -> Dict[str, Any]:
    kind = str(step.get("type") or "")
    out: Dict[str, Any] = {"id": _sid("m", index), "origin": dict(step)}
    try:
        from .quick_actions import describe_step
        label = describe_step(step, lang)
    except Exception:
        label = kind
    if kind == "command":
        out.update(type="tool", action=step.get("action") or "", params=dict(step.get("params") or {}))
    elif kind == "ask":
        out.update(type="model", prompt=step.get("prompt") or step.get("text") or "")
    elif kind == "wait":
        out.update(type="wait", seconds=float(step.get("seconds") or (float(step.get("ms") or 1000) / 1000.0)))
    elif kind == "dfy":
        out.update(type="dfy", goal=step.get("goal") or step.get("prompt") or step.get("text") or "")
    else:
        out.update(type="tool", action=f"macro.{kind}")
    out["label"] = label
    if step.get("continue_on_error"):
        out["continue_on_error"] = True
    return out

def macro_to_flow(action: Dict[str, Any], lang: str = "ru") -> Dict[str, Any]:
    steps = [macro_step_to_flow(s, i, lang) for i, s in enumerate(action.get("steps") or []) if isinstance(s, dict)]
    return {"source": "macro", "id": str(action.get("id") or ""), "name": str(action.get("label") or ""),
            "enabled": True, "steps": steps, "starts": {"hotkey": action.get("hotkey") or ""},
            "editable_in": "quick_actions"}

def flow_to_scenario_steps(flow: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The reverse of :func:`scenario_to_flow` (lossless for unchanged steps)."""
    out = []
    for step in flow.get("steps") or []:
        if isinstance(step.get("origin"), dict):
            out.append(dict(step["origin"]))
        elif step.get("type") == "tool":
            out.append({"type": "action", "action": step.get("action"), "params": step.get("params") or {}})
        elif step.get("type") == "wait":
            out.append({"type": "wait", "seconds": step.get("seconds", 1)})
        elif step.get("type") == "model":
            out.append({"type": "ask_ai", "prompt": step.get("prompt", "")})
        elif step.get("type") == "notify":
            out.append({"type": "notify", "text": step.get("text", "")})
    return out

def flow_to_macro_steps(flow: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for step in flow.get("steps") or []:
        if isinstance(step.get("origin"), dict):
            out.append(dict(step["origin"]))
        elif step.get("type") == "tool":
            out.append({"type": "command", "action": step.get("action"), "params": step.get("params") or {}})
        elif step.get("type") == "wait":
            out.append({"type": "wait", "seconds": step.get("seconds", 1)})
        elif step.get("type") == "model":
            out.append({"type": "ask", "prompt": step.get("prompt", ""), "mode": "silent"})
    return out

def as_task(flow: Dict[str, Any], lang: str = "ru") -> Dict[str, Any]:
    """«Сделать задачей»: a task draft whose single step runs the scenario / button by its own runner."""
    step = {"type": "qa_command", "command": flow["name"] or flow["id"]} if flow["source"] == "scenario" else \
        {"type": "qa_macro", "action_id": flow["id"]}
    return {"name": flow["name"] or tr(lang, "Сценарий", "Scenario"), "kind": "flow", "steps": [step],
            "rule": {"when": [{"type": "daily", "times": ["09:00"]}]}, "enabled": False}

def list_automations(lang: str = "ru") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        from . import assistant_commands as ac
        for command in ac.engine().store.load().get("commands") or []:
            if isinstance(command, dict):
                out.append(scenario_to_flow(command, lang))
    except Exception as exc:  # noqa: BLE001
        log.info("scenarios unavailable: %s", exc)
    try:
        from . import quick_canvas
        doc = quick_canvas.load()
        for action in doc.get("actions") or []:
            if isinstance(action, dict):
                out.append(macro_to_flow(action, lang))
    except Exception as exc:  # noqa: BLE001
        log.info("quick actions unavailable: %s", exc)
    return out

async def _run_macro(ref: str, lang: str) -> Tuple[bool, str, Dict[str, Any]]:
    from . import quick_actions as qa
    from . import quick_canvas
    doc = await asyncio.to_thread(quick_canvas.load)
    action = next((a for a in doc.get("actions") or [] if a.get("id") == ref), None) or \
        next((a for a in doc.get("actions") or [] if str(a.get("label") or "").strip().lower() == ref.strip().lower()), None)
    if action is None:
        return False, tr(lang, f"Кнопка «{ref}» не найдена", f"Button “{ref}” not found"), {}
    settings = quick_canvas.RUNNER.engine_settings()
    if qa.risks(action, lang, settings) and action.get("approved") != qa.signature(action):
        return False, tr(lang, "У кнопки есть шаги, требующие разрешения: запустите её один раз в Быстром доступе и разрешите",
                         "The button has steps that need permission: run it once in Quick Access and allow them"), \
            {"label": action.get("label")}
    if quick_canvas.needs_window(action, settings):
        return False, tr(lang, "Этой кнопке нужно окно Быстрого доступа (ответ в окне, вставка текста или браузер) — "
                               "по расписанию она не запускается", "This button needs the Quick Access window — it cannot run on schedule"), \
            {"label": action.get("label")}
    run = quick_canvas.RUNNER.start(action, lang=lang, context={})
    started = time.monotonic()
    while run.status in ("running", "confirm"):
        if run.status == "confirm" and time.monotonic() - started > 120:
            quick_canvas.RUNNER.cancel(run.id)
            return False, tr(lang, "Кнопка ждала подтверждения и была остановлена", "The button waited for a confirmation and was stopped"), {}
        await asyncio.sleep(0.5)
    lines = [f"{'✓' if s.get('status') == 'done' else '✗' if s.get('status') == 'error' else '–'} {s.get('title')}"
             + (f": {s.get('message')}" if s.get("message") else "") for s in run.steps]
    if run.output:
        lines.append("\n" + run.output)
    return run.status == "done", "\n".join(lines).strip() or run.error, {"label": action.get("label")}

async def _run_action(action_id: str, params_text: str, lang: str) -> Tuple[bool, str, Dict[str, Any]]:
    from . import assistant_catalog as catalog
    from . import assistant_commands as ac
    try:
        params = json.loads(params_text) if params_text else {}
    except ValueError:
        params = {}
    meta = catalog.BY_ID.get(action_id)
    if not meta:
        return False, tr(lang, f"Неизвестное действие «{action_id}»", f"Unknown action “{action_id}”"), {}
    tier = str(meta.get("tier") or "safe")
    card = await ac.engine().run(action_id, params if isinstance(params, dict) else {}, source="routine-bg", lang=lang,
                                 confirmed=tier != "dangerous", emit=True)
    status = card.get("status")
    text = str(card.get("message") or "")
    if card.get("answer"):
        text += "\n" + str(card["answer"])
    if status == "confirm":
        text += "\n" + tr(lang, "Ждёт подтверждения в окне быстрого доступа", "Waiting for confirmation in Quick Access")
    return status == "done", text.strip(), {"action": action_id}

async def run_handoff(item: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """``schedule_handoff.json`` item of kind ``macro`` / ``action`` (from a flow step running in the Proxy)."""
    task = item.get("task") or {}
    lang = "en" if task.get("lang") == "en" else "ru"
    ref = str(task.get("command") or "")
    if item.get("kind") == "macro":
        return await _run_macro(ref, lang)
    return await _run_action(ref, str(task.get("prompt") or ""), lang)
