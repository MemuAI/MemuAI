# -*- coding: utf-8 -*-
"""Round 29: the MemuAI Proxy side of programmable schedules.

:mod:`app.scheduler` stays the owner of the loop and of legacy tasks; for a task
with a ``rule`` it asks this module (``prepare_tick`` → ``decide_task``) and for the
new actions it calls ``run_action``:

* ``notify``   — a plain notification (Windows / Telegram / e-mail), no model;
* ``memucode`` — one headless MemuCode turn in a folder (``memucode -p``), the
  transcript stays a normal MemuCode session the user can resume;
* ``dfy`` / ``command`` — need the MemuAI window process (Do For You's browser,
  Quick Access commands): handed over through ``schedule_handoff.json`` and
  executed by :mod:`app.schedule_rules_api` inside MemuAI, the Proxy waits for the
  outcome so results, notifications and chains stay in one place.

The loop sleeps adaptively (``sleep_until_next``): 30 s normally, 15 s while a rule
needs the condition sampler, earlier when a slot is due, and it wakes within ~2 s
when ``schedules.json`` changes (a «Run now» click, a new task, a finished chain).
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import schedule_rules as rules
from .schedule_sampler import SAMPLER, empty, merge_needs

log = logging.getLogger("memuai.schedule_runtime")

RUNTIME_KINDS = ("notify", "dfy", "command", "memucode", "integration")
BASE_SLEEP = 30.0
SAMPLED_SLEEP = 15.0
WAKE_POLL = 2.0
HANDOFF_FILE = "schedule_handoff.json"
HANDOFF_CLAIM_TIMEOUT = 90.0
TIMEOUTS = {"dfy": 60 * 60.0, "command": 15 * 60.0, "integration": 5 * 60.0}
MEMUCODE_TIMEOUT = 30 * 60.0
PROCESS_STARTED = time.time()

#: Meta of the run being executed (test run, late, trigger) — read by scheduler._record_result.
RUN_META: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar("memuai_schedule_run_meta", default={})
_STATE: Dict[str, Any] = {"sampling": False, "wake": None}


def _memu_dir() -> Path:
    from .pgdata import MEMU_DIR
    return Path(MEMU_DIR)


def _lang() -> str:
    try:
        from .pgdata import load_preferences
        return "en" if load_preferences().get("language") == "en" else "ru"
    except Exception:
        return "ru"


def tr(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru


# --------------------------------------------------------------------------- tick

def _rule_tasks(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for task in tasks:
        if not isinstance(task.get("rule"), dict):
            continue
        enabled = task.get("enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() not in ("", "0", "false", "no", "off")
        if enabled or task.get("run_now"):
            out.append(task)
    return out


async def prepare_tick(now: float) -> Dict[str, Any]:
    """Measure what the enabled rules need — outside the schedules.json transaction."""
    from .pgdata import load_schedules
    tasks = await asyncio.to_thread(load_schedules)
    needed = []
    for task in _rule_tasks(tasks):
        try:
            needed.append(rules.needs(rules.normalize_rule(task["rule"]), str(task.get("kind") or "chat")))
        except rules.RuleError:
            continue
    needs = merge_needs(needed)
    _STATE["sampling"] = not empty(needs)
    prev, cur = await asyncio.to_thread(SAMPLER.get, needs, now=now)
    lang = await asyncio.to_thread(_lang)
    try:   # Round 35: data / integration watches — polled in worker threads, pending events → the sample
        from . import schedule_triggers_ext
        watch = await schedule_triggers_ext.tick(tasks, now, lang)
    except Exception as exc:  # noqa: BLE001 - a broken source never stops the scheduler
        log.warning("schedule: trigger watch tick failed: %s", exc)
        watch = {}
    if watch:
        cur = dict(cur or {}, watch=watch)
    return {"sample": cur, "prev": prev, "lang": lang, "cycles": rules.chain_cycles(tasks)}


def decide_task(task: Dict[str, Any], now: float, ctx: Dict[str, Any], *, running: bool,
                tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_id = {str(t.get("id")): t for t in tasks}
    return rules.decide(task, now, ctx.get("sample") or {}, ctx.get("prev"), tasks_by_id=by_id, running=running,
                        cycles=ctx.get("cycles") or set(), process_started=PROCESS_STARTED, lang=ctx.get("lang", "ru"))


def wake() -> None:
    event = _STATE.get("wake")
    if event is not None:
        try:
            event.set()
        except Exception:
            pass


def _schedules_mtime() -> Any:
    marks = []
    # Round 35 (flows): an answered «спросить меня» form (ask_user.json) wakes the loop too — the paused run goes on
    for name in ("schedules.json", "ask_user.json"):
        try:
            marks.append((_memu_dir() / name).stat().st_mtime_ns)
        except OSError:
            marks.append(0)
    return tuple(marks)


def _earliest_due() -> Optional[float]:
    try:
        from .pgdata import load_schedules
        tasks = load_schedules()
    except Exception:
        return None
    best = None
    for task in tasks:
        if task.get("enabled") is False and not task.get("run_now"):
            continue
        if task.get("run_now"):
            return time.time()
        values = [task.get("next_run")]
        state = task.get("rule_state") if isinstance(task.get("rule_state"), dict) else {}
        values.append(state.get("event_at"))
        for value in values:
            if isinstance(value, (int, float)) and (best is None or value < best):
                best = float(value)
    return best


async def sleep_until_next(default: float = BASE_SLEEP) -> None:
    """Sleep until the next useful tick (see the module docstring)."""
    if _STATE.get("wake") is None:
        _STATE["wake"] = asyncio.Event()
    event: asyncio.Event = _STATE["wake"]
    now = time.time()
    span = min(default, SAMPLED_SLEEP) if _STATE.get("sampling") else default
    earliest = await asyncio.to_thread(_earliest_due)
    if earliest is not None and earliest - now < span:
        span = max(1.0, earliest - now + 0.3)
    deadline = time.monotonic() + span
    mark = await asyncio.to_thread(_schedules_mtime)
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return
        try:
            await asyncio.wait_for(event.wait(), timeout=min(WAKE_POLL, left))
            event.clear()
            return
        except asyncio.TimeoutError:
            pass
        current = await asyncio.to_thread(_schedules_mtime)
        if current != mark:
            return


# --------------------------------------------------------------------------- finish bookkeeping

def note_finished(task_id: str, ok: bool, *, test: bool = False, text: str = "") -> None:
    """Store ``last_status``/``last_finished`` (chains and «stop after N failures» read them)."""
    from .pgdata import _mutate_schedules
    now = time.time()

    def update(tasks: List[Dict[str, Any]]) -> bool:
        for task in tasks:
            if str(task.get("id") or "") == str(task_id):
                rules.note_finished(task, ok, now, test=test, text=text)
                return True
        return False

    try:
        _mutate_schedules(update)
    except Exception as exc:  # noqa: BLE001
        log.warning("schedule: could not store the outcome of %s: %s", task_id, exc)
    wake()


def restore_rule_claim(task: Dict[str, Any], started_at: float) -> None:
    """A rule run cancelled by shutdown: make it due again on the next start."""
    task["enabled"] = True
    task["run_count"] = max(0, int(task.get("run_count") or 1) - 1)
    task["next_run"] = started_at
    state = task.get("rule_state") if isinstance(task.get("rule_state"), dict) else {}
    state["deadline"] = started_at + 12 * 3600
    state["checked"] = 0
    state["status"] = "waiting"
    task["rule_state"] = state


# --------------------------------------------------------------------------- notifications

async def notify(task: Dict[str, Any], ok: bool, text: str, *, prefs: Optional[Dict[str, Any]] = None,
                 windows: bool = True) -> None:
    from .pgdata import load_preferences
    prefs = prefs if prefs is not None else await asyncio.to_thread(load_preferences)
    lang = "en" if prefs.get("language") == "en" else "ru"
    name = str(task.get("name") or tr(lang, "Задача", "Task"))
    head = (tr(lang, "Планирование · ", "Planning · ") + name) if ok else \
        (tr(lang, "Планирование · ошибка · ", "Planning · failed · ") + name)
    body = text or tr(lang, "Задача выполнена.", "Task completed.")
    if windows and prefs.get("notify_windows", True):
        try:
            from .desktop_notifications import show_completion
            await asyncio.to_thread(show_completion, "MemuAI · " + name, body[:400],
                                    sound=bool(prefs.get("notify_sound", False)))
        except Exception as exc:  # noqa: BLE001
            log.info("schedule: Windows notification not shown (%s)", type(exc).__name__)
    if task.get("notify_telegram"):
        try:
            from .telegram_bot import send_notification
            await send_notification(head, body, model=str(task.get("model") or ""), kind="schedule")
        except Exception as exc:  # noqa: BLE001
            log.info("schedule: Telegram notification not sent (%s)", type(exc).__name__)
    if task.get("notify_email") and prefs.get("schedule_email_enabled", False):
        try:
            from .scheduler import _send_schedule_email
            await asyncio.to_thread(_send_schedule_email, prefs, "MemuAI · " + name, body)
        except Exception as exc:  # noqa: BLE001
            log.info("schedule: e-mail not sent (%s)", type(exc).__name__)


async def _notify_only(task: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """``kind: notify`` — the reminder text, delivered without a model."""
    from .pgdata import load_preferences
    prefs = await asyncio.to_thread(load_preferences)
    lang = "en" if prefs.get("language") == "en" else "ru"
    text = str(task.get("prompt") or task.get("reminder_text") or "").strip() or "⏰"
    delivered: List[str] = []
    problems: List[str] = []
    try:
        from .desktop_notifications import show_completion
        await asyncio.to_thread(show_completion, "MemuAI · " + str(task.get("name") or tr(lang, "Напоминание", "Reminder")),
                                text[:400], sound=True)
        delivered.append("windows")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"Windows: {type(exc).__name__}")
    if task.get("notify_telegram"):
        try:
            sent, info = await _telegram_text(prefs, text, lang)
            (delivered if sent else problems).append("telegram" if sent else f"Telegram: {info}")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"Telegram: {exc}"[:200])
    if task.get("notify_email"):
        if prefs.get("schedule_email_enabled", False):
            try:
                from .scheduler import _send_schedule_email
                await asyncio.to_thread(_send_schedule_email, prefs, "MemuAI · " + str(task.get("name") or ""), text)
                delivered.append("email")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"Email: {exc}"[:200])
        else:
            problems.append(tr(lang, "Почта: отправка выключена в настройках", "Email: delivery is off in Settings"))
    ok = bool(delivered) and not (task.get("notify_telegram") and "telegram" not in delivered)
    out = text if not problems else text + "\n\n" + "; ".join(problems)
    return ok, out, {"kind": "notify", "delivered": delivered, "notified": True}


async def _telegram_text(prefs: Dict[str, Any], text: str, lang: str) -> Tuple[bool, str]:
    token = str(prefs.get("telegram_bot_token") or "").strip()
    chat_id = str(prefs.get("telegram_chat_id") or "").strip()
    if not (prefs.get("telegram_enabled") and token and chat_id):
        return False, tr(lang, "бот Telegram не подключён", "the Telegram bot is not paired")
    import httpx
    from . import telegram_bot
    try:
        quiet = bool(telegram_bot._in_quiet_hours(prefs))
    except Exception:
        quiet = False
    body = "⏰ **" + tr(lang, "Напоминание", "Reminder") + "**\n\n" + text
    async with httpx.AsyncClient() as client:
        await telegram_bot._send(client, token, chat_id, body, None, markdown=True, silent=quiet)
    return True, "telegram"


# --------------------------------------------------------------------------- MemuCode

async def _memucode(task: Dict[str, Any], *, popen: Any = subprocess.Popen) -> Tuple[bool, str, Dict[str, Any]]:
    lang = await asyncio.to_thread(_lang)
    root = str(task.get("project_path") or "").strip()
    if not root or not Path(root).is_dir():
        return False, tr(lang, "Папка проекта MemuCode не найдена: ", "The MemuCode project folder was not found: ") + root, {}
    prompt = str(task.get("prompt") or "").strip()
    if not prompt:
        return False, tr(lang, "Пустое задание для MemuCode", "Empty MemuCode task"), {}
    from .tgapp_code import memucode_command
    command = memucode_command()
    if not command:
        return False, tr(lang, "MemuCode CLI не установлен", "MemuCode CLI is not installed"), {}
    mode = str(task.get("permission_mode") or "plan")
    if mode not in ("default", "plan", "acceptEdits", "bypassPermissions"):
        mode = "plan"
    args = [*command, "-p", "--output-format", "stream-json", "--cwd", root]
    if mode != "default":
        args += ["--permission-mode", mode]
    if task.get("model"):
        args += ["-m", str(task["model"])]
    if lang == "en":
        args += ["--lang", "en"]
    args += ["--", prompt]
    env = dict(os.environ)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "MEMUCODE_NO_REMOTE": "1", "NO_COLOR": "1"})
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

    def run() -> Tuple[int, List[Dict[str, Any]], str]:
        process = popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, cwd=root,
                        env=env, creationflags=flags, text=True, encoding="utf-8", errors="replace")
        try:
            out, err = process.communicate(timeout=MEMUCODE_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            out, err = process.communicate()
            err = (err or "") + "\ntimeout"
        events = []
        for line in (out or "").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                events.append(event)
        return process.returncode, events, err or ""

    code, events, err = await asyncio.to_thread(run)
    text = "".join(str(e.get("text") or "") for e in events if e.get("type") == "text").strip()
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    session = next((str(e.get("session_id")) for e in events if e.get("session_id")), "")
    tools = sum(1 for e in events if e.get("type") == "tool_use")
    ok = code == 0 and not result.get("is_error")
    if not text:
        text = str(result.get("result") or "").strip()
    if not ok and not text:
        text = (err.strip().splitlines()[-1:] or [f"exit code {code}"])[0][:600]
    extra = {"kind": "memucode", "memucode": {"root": root, "session_id": session, "tools": tools, "mode": mode}}
    return ok, text[:20000], extra


# --------------------------------------------------------------------------- handoff (MemuAI window process)

def handoff_path() -> Path:
    return _memu_dir() / HANDOFF_FILE


def _handoff_mutate(fn) -> Any:
    from .storage import mutate_json

    def update(raw: Any):
        data = raw if isinstance(raw, dict) else {}
        items = data.get("items") if isinstance(data.get("items"), list) else []
        data["items"] = [item for item in items if isinstance(item, dict)][-50:]
        changed = fn(data["items"])
        return bool(changed) or raw != data, data

    return mutate_json(handoff_path(), {"items": []}, update, backup=False,
                       validator=lambda value: isinstance(value, dict), repair_invalid=True)


def handoff_items() -> List[Dict[str, Any]]:
    try:
        from .storage import load_json_with_recovery
        data = load_json_with_recovery(handoff_path(), {"items": []}, validator=lambda value: isinstance(value, dict))
    except Exception:
        return []
    items = data.get("items") if isinstance(data, dict) else []
    return [item for item in items or [] if isinstance(item, dict)]


async def _handoff(task: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    lang = await asyncio.to_thread(_lang)
    kind = str(task.get("kind"))
    item_id = uuid.uuid4().hex[:12]
    payload = {key: task.get(key) for key in ("id", "name", "prompt", "command", "command_id", "model", "lang",
                                            "tool", "args")}
    now = time.time()

    def add(items: List[Dict[str, Any]]) -> bool:
        cutoff = time.time() - 6 * 3600
        items[:] = [it for it in items if float(it.get("updated") or it.get("created") or 0) > cutoff
                    or it.get("status") in ("queued", "running")]
        items.append({"id": item_id, "task_id": str(task.get("id") or ""), "kind": kind, "task": payload,
                      "status": "queued", "created": now, "updated": now})
        return True

    await asyncio.to_thread(_handoff_mutate, add)
    limit = TIMEOUTS.get(kind, 15 * 60.0)
    started = time.monotonic()
    try:
        while True:
            await asyncio.sleep(1.5)
            items = await asyncio.to_thread(handoff_items)
            item = next((it for it in items if it.get("id") == item_id), None)
            if item is None:
                return False, tr(lang, "Задача пропала из очереди MemuAI", "The task vanished from the MemuAI queue"), {}
            status = item.get("status")
            if status == "done":
                await asyncio.to_thread(_handoff_mutate, lambda rows: _drop(rows, item_id))
                extra = dict(item.get("extra") or {})
                extra.setdefault("kind", kind)
                return bool(item.get("ok")), str(item.get("text") or ""), extra
            elapsed = time.monotonic() - started
            if status == "queued" and elapsed > HANDOFF_CLAIM_TIMEOUT:
                await asyncio.to_thread(_handoff_mutate, lambda rows: _drop(rows, item_id))
                return False, tr(lang, "Окно MemuAI не открыто — действие не выполнено",
                                 "The MemuAI window is not open — the action did not run"), {"kind": kind}
            if elapsed > limit:
                await asyncio.to_thread(_handoff_mutate, lambda rows: _mark(rows, item_id, cancel=True))
                return False, tr(lang, "Превышено время выполнения — задача остановлена",
                                 "Time limit exceeded — the task was stopped"), {"kind": kind}
    except asyncio.CancelledError:
        try:
            await asyncio.to_thread(_handoff_mutate, lambda rows: _mark(rows, item_id, cancel=True))
        except Exception:
            pass
        raise


def _drop(items: List[Dict[str, Any]], item_id: str) -> bool:
    before = len(items)
    items[:] = [it for it in items if it.get("id") != item_id]
    return len(items) != before


def _mark(items: List[Dict[str, Any]], item_id: str, **fields: Any) -> bool:
    for item in items:
        if item.get("id") == item_id:
            item.update(fields)
            item["updated"] = time.time()
            return True
    return False


# --------------------------------------------------------------------------- actions

async def run_action(task: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    kind = str(task.get("kind") or "")
    try:
        if kind == "notify":
            return await _notify_only(task)
        if kind == "memucode":
            return await _memucode(task)
        if kind in rules.APP_ACTIONS:
            return await _handoff(task)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("schedule action %s failed: %s", kind, exc)
        return False, f"{type(exc).__name__}: {exc}"[:600], {"kind": kind}
    return False, "unknown action", {"kind": kind}
