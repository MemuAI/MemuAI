# -*- coding: utf-8 -*-
"""Планировщик MemuAI: фоновое выполнение задач по расписанию."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import smtplib
import ssl
import time
import uuid
from datetime import datetime, time as dtime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict

from .pgdata import _mutate_schedules, append_schedule_result, load_preferences

log = logging.getLogger("memuai.scheduler")
_RUNNING_TASK_IDS: set[str] = set()
_ACTIVE_TASKS: set[asyncio.Task[Any]] = set()


_WORK_OPEN = re.compile(r"^(?P<fence>`{3,})memuai-work[ \t]*$", re.I)
_FENCE_LINE = re.compile(r"^(`{3,})([^`\s]*)[ \t]*$")


def _work_blocks(text: str) -> list[tuple[int, int, str, str]]:
    """Находит блоки memuai-work: (начало, конец, мета-JSON, содержимое).

    Блок закрывается строкой из такого же числа обратных кавычек. Вложенные
    ```lang … ``` внутри файла (README с примерами) учитываются, поэтому
    файл не обрезается на первой же внутренней тройке.
    """
    lines = str(text or "").splitlines(keepends=True)
    offsets, position = [], 0
    for line in lines:
        offsets.append(position)
        position += len(line)
    blocks: list[tuple[int, int, str, str]] = []
    index = 0
    while index < len(lines):
        opened = _WORK_OPEN.match(lines[index].rstrip("\r\n"))
        if not opened or index + 1 >= len(lines):
            index += 1
            continue
        fence = opened.group("fence")
        meta = lines[index + 1].strip()
        depth, cursor = 0, index + 2
        while cursor < len(lines):
            match = _FENCE_LINE.match(lines[cursor].rstrip("\r\n"))
            if match and len(match.group(1)) >= len(fence):
                if match.group(2):
                    depth += 1
                elif depth:
                    depth -= 1
                elif len(match.group(1)) == len(fence):
                    break
            cursor += 1
        if cursor >= len(lines):
            break  # незакрытый блок — ничего не пишем
        # Перевод строки перед закрывающей ``` — последняя строка файла.
        content = "".join(lines[index + 2:cursor])
        end = offsets[cursor] + len(lines[cursor])
        blocks.append((offsets[index], end, meta, content))
        index = cursor + 1
    return blocks


def _scheduled_work_actions(text: str) -> tuple[str, list[dict[str, str]]]:
    files: list[dict[str, str]] = []
    total = 0
    source = str(text or "")
    blocks = _work_blocks(source)
    for _start, _end, raw_meta, content in blocks:
        try:
            meta = json.loads(raw_meta)
            rel = str(meta.get("path") or "").replace("\\", "/").strip()
            if (not rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel)
                    or ".." in rel.split("/") or len(content) > 1_500_000
                    or total + len(content) > 5_000_000):
                continue
            files.append({"path": rel, "content": content})
            total += len(content)
        except Exception:
            continue
        if len(files) >= 30:
            break
    cleaned = source
    for start, end, _meta, _content in reversed(blocks):
        cleaned = cleaned[:start] + cleaned[end:]
    return cleaned.strip(), files


def _work_project(project_id: str) -> dict[str, Any]:
    from .pgdata import load_workspaces
    data = load_workspaces()
    project = next((item for item in data.get("items", [])
                    if str(item.get("id") or "") == str(project_id or "")), None)
    if not project or not Path(str(project.get("path") or "")).is_dir():
        raise FileNotFoundError("проект Work для расписания недоступен")
    return project


def _apply_scheduled_work(project: dict[str, Any], files: list[dict[str, str]]) -> int:
    root = Path(str(project["path"])).resolve()
    staged: list[tuple[Path, Path]] = []
    try:
        for item in files:
            target = (root / item["path"]).resolve()
            target.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + f".memuai-{uuid.uuid4().hex[:8]}.tmp")
            # newline="" — без подмены \n на \r\n в Windows (как в work_tools).
            with temporary.open("w", encoding="utf-8", newline="") as handle:
                handle.write(item["content"])
            staged.append((temporary, target))
        for temporary, target in staged:
            os.replace(temporary, target)
        return len(staged)
    finally:
        for temporary, _ in staged:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _next_run_for(task: Dict[str, Any], *, now: float | None = None) -> float:
    now = _finite_number(now) if now is not None else time.time()
    if now is None:
        now = time.time()
    fallback = now + 3600
    run_at = task.get("run_at")
    if run_at not in (None, ""):
        try:
            if isinstance(run_at, (int, float)):
                timestamp = float(run_at)
            else:
                timestamp = datetime.fromisoformat(str(run_at).replace("Z", "+00:00")).timestamp()
            return timestamp if math.isfinite(timestamp) else fallback
        except (OSError, OverflowError, ValueError, TypeError):
            return fallback
    interval = task.get("interval_minutes")
    if interval not in (None, ""):
        minutes = _finite_number(interval)
        return now + minutes * 60 if minutes is not None and minutes > 0 else fallback
    daily_at = task.get("daily_at")
    if daily_at not in (None, ""):
        try:
            hh, mm = str(daily_at).split(":")[:2]
            hour, minute = int(hh), int(mm)
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                return fallback
            # Следующий календарный день, а не now + 24 ч: в дни перехода на
            # летнее/зимнее время сутки длятся 23 или 25 часов.
            today = datetime.fromtimestamp(now).date()
            for offset in range(0, 3):
                timestamp = datetime.combine(today + timedelta(days=offset),
                                             dtime(hour, minute)).timestamp()
                if timestamp > now:
                    return timestamp
            return fallback
        except (OSError, OverflowError, ValueError, TypeError):
            return fallback
    return fallback


def _task_enabled(task: Dict[str, Any]) -> bool:
    value = task.get("enabled", True)
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def _stored_next_run(task: Dict[str, Any]) -> float | None:
    value = _finite_number(task.get("next_run"))
    return value if value is not None and value > 0 else None


def _record_result(task_id: str, ok: bool, text: str,
                   extra: Dict[str, Any] | None = None, *, meta: Dict[str, Any] | None = None) -> None:
    # Round 29 (programmable schedules): a test run / late run / trigger is marked in the history, and the
    # outcome is stored on the task (``last_status``/``last_finished``) for chains and failure limits.
    if meta is None:
        from .schedule_runtime import RUN_META
        meta = RUN_META.get()
    if meta:
        extra = {**(extra or {}), **{k: v for k, v in meta.items() if k in ("test", "late", "trigger") and v}}
    result_id = ""
    try:
        if extra:
            result_id = append_schedule_result(task_id, ok, text, extra)
        else:
            result_id = append_schedule_result(task_id, ok, text)
    except Exception as exc:  # noqa: BLE001
        log.warning("scheduler: could not store the result of task %s: %s", task_id, exc)
    try:   # Round 35: result / failure / Work changes to approve → «Входящие» (app/inbox_hooks.py)
        from .inbox_hooks import schedule_result
        schedule_result(task_id, ok, text, {**(extra or {}), "result_id": result_id or ""}, meta)
    except Exception:  # noqa: BLE001
        pass
    try:
        from .schedule_runtime import note_finished
        note_finished(task_id, ok, test=bool((meta or {}).get("test")), text=str(text or ""))
    except Exception as exc:  # noqa: BLE001
        log.warning("scheduler: could not store the outcome of task %s: %s", task_id, exc)


SMTP_TIMEOUT = 20.0
#: Well-known ports whose TLS mode is fixed by convention: 465 is implicit TLS
#: (SMTPS), 587/25 are plain SMTP upgraded with STARTTLS.
_IMPLICIT_TLS_PORTS = {465}
_STARTTLS_PORTS = {25, 587, 2525}


def smtp_settings(prefs: Dict[str, Any]) -> Dict[str, Any]:
    """Validated SMTP connection settings (raises ``ValueError`` with a readable message).

    Round 23: a non-numeric port used to escape as a bare ``ValueError: invalid
    literal for int()``, and "STARTTLS on 465" waited for a plaintext banner the
    server never sends — every scheduled e-mail hung for the whole socket timeout.
    The mode now follows the port when the two contradict each other.
    """
    host = str(prefs.get("smtp_host") or "").strip()
    raw_port = prefs.get("smtp_port")
    if raw_port in (None, ""):
        port = 587
    else:
        try:
            port = int(str(raw_port).strip())
        except (TypeError, ValueError):
            raise ValueError(f"SMTP-порт должен быть числом от 1 до 65535, а не {str(raw_port)[:40]!r}") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"SMTP-порт должен быть числом от 1 до 65535, а не {port}")
    security = str(prefs.get("smtp_security") or "starttls").strip().lower()
    if security not in ("starttls", "ssl", "none"):
        security = "starttls"
    adjusted = ""
    if security == "starttls" and port in _IMPLICIT_TLS_PORTS:
        security, adjusted = "ssl", "порт 465 — это SSL/TLS, а не STARTTLS"
    elif security == "ssl" and port in _STARTTLS_PORTS:
        security, adjusted = "starttls", f"порт {port} — это STARTTLS, а не SSL/TLS"
    if host and any(ch in host for ch in " /\\\r\n"):
        raise ValueError("некорректный адрес SMTP-сервера")
    return {"host": host, "port": port, "security": security, "adjusted": adjusted}


def _send_schedule_email(prefs: Dict[str, Any], title: str, text: str,
                         *, timeout: float = SMTP_TIMEOUT) -> None:
    """Send one result through the user's SMTP server without extra dependencies."""
    conf = smtp_settings(prefs)
    host = conf["host"]
    sender = str(prefs.get("smtp_from") or prefs.get("smtp_user") or "").strip()
    recipients = [part.strip() for part in str(prefs.get("schedule_email_to") or "").replace(";", ",").split(",") if part.strip()]
    if not host or not sender or not recipients:
        raise ValueError("не заполнены SMTP-сервер, отправитель или получатель")
    if any("\r" in value or "\n" in value for value in [sender, *recipients]):
        raise ValueError("некорректный адрес электронной почты")
    if conf["adjusted"]:
        log.info("scheduler: SMTP mode adjusted: %s", conf["adjusted"])
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = str(title or "MemuAI · Планирование").replace("\r", " ").replace("\n", " ")[:180]
    message.set_content(text or "Задача выполнена.")
    port, security = conf["port"], conf["security"]
    context = ssl.create_default_context()
    try:
        if security == "ssl":
            smtp_connection = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
        else:
            smtp_connection = smtplib.SMTP(host, port, timeout=timeout)
    except ssl.SSLError as exc:
        raise ConnectionError(f"SMTP {host}:{port}: TLS не согласован ({getattr(exc, 'reason', None) or exc}) — "
                              "проверьте режим SSL/TLS или STARTTLS") from None
    except (TimeoutError, OSError) as exc:
        raise ConnectionError(f"SMTP {host}:{port} не отвечает ({type(exc).__name__}) — "
                              "проверьте адрес, порт и режим шифрования") from None
    with smtp_connection as smtp:
        if security == "starttls":
            try:
                smtp.starttls(context=context)
            except smtplib.SMTPNotSupportedError:
                raise ConnectionError(f"SMTP {host}:{port} не поддерживает STARTTLS — выберите SSL/TLS "
                                      "(обычно порт 465) или «без шифрования»") from None
        user = str(prefs.get("smtp_user") or "").strip()
        password = str(prefs.get("smtp_password") or "")
        if user:
            smtp.login(user, password)
        smtp.send_message(message, to_addrs=recipients)


async def _run_task(task: Dict[str, Any]) -> None:
    from .schedule_runtime import RUNTIME_KINDS, RUN_META, notify as _rule_notify, run_action
    meta = task.get("_run_meta") if isinstance(task.get("_run_meta"), dict) else {}
    RUN_META.set(meta)
    if meta.get("payload"):
        # Round 35 (triggers): task["trigger"] = the event data; a plain prompt gets it (placeholders or context)
        from .schedule_triggers_ext import apply_trigger
        task = apply_trigger(task)
    try:   # Round 37: {{date}}, {{time}}, {{weekday}}, {{last_result}}… in a plain prompt / reminder text
        from .schedule_conds_ext import render_vars
        from .schedule_runtime import _lang as _plan_lang
        task = render_vars(task, lang=_plan_lang())
    except Exception as exc:  # noqa: BLE001
        log.debug("scheduler: template variables failed: %s", exc)
    if isinstance(task.get("steps"), list) and task.get("steps"):
        # Round 35 (flows): a task with steps — app/flow_engine.py records the result, chains and notifications.
        from .flow_engine import run_task_flow
        await run_task_flow({k: v for k, v in task.items() if k != "_run_meta"}, meta)
        return
    if str(task.get("kind") or "") in RUNTIME_KINDS:
        # Round 29: notify / Do For You / Quick Access command / MemuCode (app/schedule_runtime.py).
        ok, text, extra = await run_action(task)
        await asyncio.to_thread(_record_result, str(task.get("id") or ""), ok, text, extra, meta=meta)
        if not extra.get("notified"):
            await _rule_notify(task, ok, text)
        return
    if str(task.get("kind") or "") == "reminder":
        # Раунд 22: напоминание из быстрого доступа («напомни в телеграме в 15:00…») —
        # готовый текст без вызова модели, см. app/assistant_commands.deliver_reminder.
        from .assistant_commands import deliver_reminder
        ok, info = await deliver_reminder(task)
        text = str(task.get("reminder_text") or task.get("prompt") or "")
        await asyncio.to_thread(_record_result, str(task.get("id") or ""), ok, text if ok else f"{text}\n\n{info}".strip())
        return
    prompt = str(task.get("prompt", ""))
    model = str(task.get("model", ""))
    mode = "work" if str(task.get("mode") or "chat").lower() == "work" else "chat"
    prefs = load_preferences()
    en = prefs.get("language") == "en"     # the result, notifications and errors are read by the user

    def tr(ru: str, en_text: str) -> str:
        return en_text if en else ru

    messages: list[dict[str, str]] = []
    project: dict[str, Any] | None = None
    if mode == "work":
        try:
            project = _work_project(str(task.get("project_id") or ""))
        except Exception as exc:  # noqa: BLE001
            text = (tr("Проект Work для расписания недоступен (папка удалена или перемещена)",
                       "The Work project of this schedule is unavailable (its folder was deleted or moved)")
                    if isinstance(exc, FileNotFoundError) else str(exc))
            await asyncio.to_thread(_record_result, str(task.get("id") or ""), False, text)
            return
        messages.append({"role": "system", "content": (
            "You are executing a scheduled MemuAI Work task in the explicitly selected project "
            f"{project.get('name')!r}. Complete only the scheduled objective. Use supplied knowledge "
            "and produce every file to create or replace as one hidden block exactly:\n"
            "```memuai-work\n{\"path\":\"relative/path.ext\"}\nCOMPLETE FILE CONTENT\n```\n"
            "Use only project-relative paths, never .. or absolute paths. Do not use browser or computer "
            "control, do not delete files, do not run commands, and do not claim that a proposed file was "
            "written. End with a concise result and verification guidance."
        )})
    messages.append({"role": "user", "content": prompt})
    parts = []
    try:
        from .chat import chat_service
        async for chunk in chat_service.stream_completion(model, messages):
            for ch in chunk.get("choices") or []:
                d = ch.get("delta") or {}
                if d.get("content"):
                    parts.append(str(d["content"]))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        await asyncio.to_thread(_record_result, str(task.get("id") or ""), False,
                                f"{type(exc).__name__}: {exc}")
        log.warning("scheduler: task '%s' failed: %s", task.get("name", ""), exc)
        return

    # Reasoning streamed as <think>…</think> (a provider's «think tags» mode or a model that inlines it) went to
    # Telegram/email, and a memuai-work draft inside it was applied as a file.
    from .assistant_commands import clean_ai_text
    text = clean_ai_text("".join(parts))
    result_extra: dict[str, Any] = {"mode": mode} if mode == "work" else {}
    if mode == "work":
        clean, files = _scheduled_work_actions(text)
        text = clean
        if files and project:
            access = str(task.get("access_mode") or prefs.get("work_access_mode") or "ask")
            if access == "full":
                try:
                    count = await asyncio.to_thread(_apply_scheduled_work, project, files)
                    text = (text + "\n\n" + tr(f"Work: применено файлов — {count}.",
                                                f"Work: files applied — {count}.")).strip()
                    result_extra.update({"project_id": project.get("id"), "work_status": "applied",
                                         "work_files": [item["path"] for item in files]})
                except Exception as exc:  # noqa: BLE001
                    await asyncio.to_thread(
                        _record_result, str(task.get("id") or ""), False,
                        tr("Не удалось применить Work-файлы: ", "Could not apply the Work files: ") + str(exc),
                        {"mode": mode, "project_id": project.get("id"), "work_status": "error"})
                    return
            else:
                text = (text + "\n\n" + tr("Work: изменения ожидают подтверждения в разделе Планирование.",
                                            "Work: the changes are waiting for approval in Planning.")).strip()
                result_extra.update({"project_id": project.get("id"), "work_status": "pending",
                                     "pending_work": files})
    # schedule_results.json may hold tens of MB of pending Work files: the
    # read-modify-write runs in a worker thread, not on the event loop.
    await asyncio.to_thread(_record_result, str(task.get("id") or ""), True, text, result_extra)
    try:
        if prefs.get("notify_windows", True):
            from .desktop_notifications import show_completion
            await asyncio.to_thread(
                show_completion,
                f"MemuAI · {task.get('name') or tr('Задача завершена', 'Task finished')}",
                text or tr("Задача выполнена.", "Task completed."),
                sound=bool(prefs.get("notify_sound", False)),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("scheduler: Windows notification not sent: %s", exc)
    title = tr("Планирование · ", "Planning · ") + str(task.get("name") or tr("Задача завершена", "Task finished"))
    if task.get("notify_telegram", True):
        try:
            from .telegram_bot import send_notification
            await send_notification(title, text, model=str(task.get("model") or ""), kind="schedule")
        except Exception as exc:  # noqa: BLE001
            log.warning("scheduler: Telegram notification not sent: %s", exc)
    if task.get("notify_email", False) and prefs.get("schedule_email_enabled", False):
        try:
            await asyncio.to_thread(_send_schedule_email, prefs,
                                    f"MemuAI · {task.get('name') or tr('Задача завершена', 'Task finished')}", text)
        except Exception as exc:  # noqa: BLE001
            log.warning("scheduler: email not sent: %s", exc)
    log.info("scheduler: task '%s' finished", task.get("name", ""))


def _persist_last_run(task_id: str, started_at: float) -> None:
    def update(tasks: list[Dict[str, Any]]) -> bool:
        for task in tasks:
            if str(task.get("id") or "") != task_id:
                continue
            current = _finite_number(task.get("last_run"))
            if current is None or started_at > current:
                task["last_run"] = started_at
                return True
            return False
        return False

    _mutate_schedules(update)


def _restore_cancelled_run(task_id: str, started_at: float) -> None:
    """Make a cancelled claim due again instead of recording it as completed."""
    def update(tasks: list[Dict[str, Any]]) -> bool:
        for task in tasks:
            if str(task.get("id") or "") != task_id:
                continue
            if _finite_number(task.get("last_run")) != started_at:
                return False
            task.pop("last_run", None)
            if isinstance(task.get("rule"), dict):
                from .schedule_runtime import restore_rule_claim
                restore_rule_claim(task, started_at)
                return True
            if task.get("run_at") not in (None, ""):
                # Разовую задачу выключил сам захват — возвращаем. Периодическую
                # мог выключить пользователь во время запуска: не трогаем.
                task["enabled"] = True
            task["next_run"] = started_at
            return True
        return False

    _mutate_schedules(update)


async def _run_claimed_task(task: Dict[str, Any], task_id: str, started_at: float) -> None:
    try:
        await _run_task(task)
    except asyncio.CancelledError:
        try:
            _restore_cancelled_run(task_id, started_at)
        except Exception as exc:  # noqa: BLE001
            log.warning("scheduler: could not restore cancelled task %s: %s", task_id, exc)
        raise
    finally:
        if not asyncio.current_task().cancelling():
            try:
                _persist_last_run(task_id, started_at)
            except Exception as exc:  # noqa: BLE001
                log.warning("scheduler: could not store last_run for task %s: %s", task_id, exc)
        _RUNNING_TASK_IDS.discard(task_id)


def _start_claimed_task(task: Dict[str, Any], task_id: str, started_at: float) -> None:
    _RUNNING_TASK_IDS.add(task_id)
    try:
        job = asyncio.create_task(_run_claimed_task(task, task_id, started_at))
    except BaseException:
        _RUNNING_TASK_IDS.discard(task_id)
        raise
    _ACTIVE_TASKS.add(job)
    job.add_done_callback(_ACTIVE_TASKS.discard)


async def _scheduler_tick(*, now: float | None = None) -> int:
    """Atomically claim due tasks and start each id at most once."""
    timestamp = _finite_number(now) if now is not None else time.time()
    if timestamp is None:
        timestamp = time.time()
    claimed: list[tuple[str, Dict[str, Any]]] = []
    # Round 29: rules with conditions/events get one shared condition sample per tick, measured
    # before the file transaction (app/schedule_runtime.py, app/schedule_sampler.py).
    from . import schedule_runtime
    try:
        rule_ctx: Dict[str, Any] | None = await schedule_runtime.prepare_tick(timestamp)
    except Exception as exc:  # noqa: BLE001
        log.warning("scheduler: condition sample failed: %s", exc)
        rule_ctx = None

    def claim(tasks: list[Dict[str, Any]]) -> bool:
        changed = False
        seen_ids: set[str] = set()
        for task in tasks:
            task_id = str(task.get("id") or "").strip()[:120]
            if not task_id or task_id in seen_ids:
                task_id = uuid.uuid4().hex[:12]
                task["id"] = task_id
                changed = True
            elif task.get("id") != task_id:
                task["id"] = task_id
                changed = True
            seen_ids.add(task_id)
            enabled = _task_enabled(task)
            if task.get("enabled") is not enabled:
                task["enabled"] = enabled
                changed = True
            last_run = _finite_number(task.get("last_run"))
            if "last_run" in task and (last_run is None or last_run < 0):
                task.pop("last_run", None)
                changed = True
            if isinstance(task.get("rule"), dict):
                if rule_ctx is None:
                    continue
                if not enabled and not task.get("run_now"):
                    if task.get("next_run") is not None:
                        task.pop("next_run", None)
                        changed = True
                    continue
                if not enabled:
                    # «Test run» of a paused rule: run once, keep it paused.
                    task.pop("run_now", None)
                    changed = True
                    if task_id not in _RUNNING_TASK_IDS:
                        claimed.append((task_id, dict(task, _run_meta={"test": True, "trigger": "manual"})))
                    continue
                decision = schedule_runtime.decide_task(task, timestamp, rule_ctx, tasks=tasks,
                                                        running=task_id in _RUNNING_TASK_IDS)
                changed = changed or decision["changed"]
                if decision["run"]:
                    run_meta = {"test": decision["test"], "late": decision["late"], "trigger": decision["trigger"]}
                    if decision.get("payload"):   # Round 35 (triggers): the event data → {{trigger.*}}
                        run_meta["payload"] = decision["payload"]
                    if decision.get("source"):    # Round 35 (finish-c): actual event type → {{trigger.type}}
                        run_meta["source"] = decision["source"]
                    claimed.append((task_id, dict(task, _run_meta=run_meta)))
                continue
            next_run = _stored_next_run(task)
            if not enabled:
                # A paused task forgets its slot: switched back on days later, a daily task used to fire at once
                # for the stale 09:00 instead of waiting for the next one. Re-enabling recomputes it.
                if "next_run" in task and task.get("next_run") is not None:
                    task.pop("next_run", None)
                    changed = True
                continue
            if next_run is None:
                task["next_run"] = _next_run_for(task, now=timestamp)
                changed = True
                continue
            if timestamp < next_run or task_id in _RUNNING_TASK_IDS:
                continue
            task["last_run"] = timestamp
            if task.get("run_at") not in (None, ""):
                task["enabled"] = False
                task["next_run"] = None
            else:
                task["next_run"] = _next_run_for(task, now=timestamp)
            claimed.append((task_id, dict(task)))
            changed = True
        return changed

    # The transaction takes a cross-process file lock (the desktop app may be
    # writing schedules.json) and parses/writes JSON: never on the event loop,
    # which also serves the Proxy API and Telegram polling.
    transaction = asyncio.ensure_future(asyncio.to_thread(_mutate_schedules, claim))
    try:
        await asyncio.shield(transaction)
    except asyncio.CancelledError:
        # Shutdown while the worker thread was claiming: let it finish, then
        # hand the claims back so a one-shot task is not left disabled
        # without ever having run.
        try:
            await transaction
        except BaseException:  # noqa: BLE001
            pass
        for task_id, _task in claimed:
            try:
                _restore_cancelled_run(task_id, timestamp)
            except Exception as exc:  # noqa: BLE001
                log.warning("scheduler: could not restore claim of task %s: %s", task_id, exc)
        raise
    try:   # Round 35 (flows): answered «спросить меня», woken waits, runs interrupted by a restart
        from .flow_engine import resume_pending
        await resume_pending()
    except Exception as exc:  # noqa: BLE001
        log.warning("scheduler: flow resume failed: %s", exc)
    started = 0
    for task_id, task in claimed:
        if task_id in _RUNNING_TASK_IDS:
            continue
        _start_claimed_task(task, task_id, timestamp)
        started += 1
    return started


async def scheduler_loop() -> None:
    """Каждые 30 секунд проверяет расписание. Хранит next_run прямо в задачах."""
    while True:
        try:
            await _scheduler_tick()
        except Exception as e:  # noqa: BLE001
            log.warning("scheduler: %s", e)
        try:
            # Round 29: 30 s, 15 s while rules sample conditions, earlier for a due slot,
            # ~2 s after schedules.json changes (Run now, a new task, a finished chain).
            from .schedule_runtime import sleep_until_next
            await sleep_until_next(30)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("scheduler: %s", e)
            await asyncio.sleep(30)
