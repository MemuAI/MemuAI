"""``/schedule`` — prompts that MemuCode runs by itself at a time.

``/schedule`` opens the list (new task, run now, last result, change, pause, delete); the quick form is
``/schedule daily 09:00 -- check the failing tests and tell me what broke``. Where a task runs — in this window
while it is open, or in the Windows Task Scheduler even when MemuCode is closed — is chosen per task. See
``schedule.py`` for how the runs work and what they are allowed to do.
"""
from __future__ import annotations

import time
from typing import List, Optional

from rich.table import Table
from rich.text import Text

from .. import schedule
from ..i18n import tr
from ..schedule import ScheduleError, Task
from ..timefmt import ago
from ..tui import dialogs as dlg
from . import Context, command

def mode_rows() -> dict:
    """The permission modes a scheduled task may have, as (label, what it means) — in the current language."""
    return {
        "plan": (tr("Только читать и исследовать", "Read and research only"),
                 tr("ничего не меняет и не запускает команды, кроме чтения; безопаснее всего", "changes nothing; safest")),
        "acceptEdits": (tr("Править файлы проекта", "Edit the project's files"),
                        tr("правки внутри папки проекта разрешены; команды и всё остальное — отказ (никто не ответит)",
                           "edits inside the project are allowed; commands and the rest are refused (nobody can answer)")),
        "auto": (tr("Авто: проверяющий одобряет обычное", "Auto: a judge approves the routine"),
                 tr("обычные команды и правки одобряет модель решений или правила; рискованное — отказ",
                    "routine commands and edits are approved by the decision model or rules; risky ones are refused")),
        "bypassPermissions": (tr("Всё без вопросов", "Everything, no questions"),
                              tr("ОПАСНО: задача может делать что угодно в вашей учётной записи",
                                 "DANGEROUS: the task may do anything your account can")),
    }

def _status_mark(task: Task) -> str:
    if not task.enabled:
        return "‖"
    return {"ok": "✓", "error": "✗", "timeout": "✗", "running": "…", "missed": "⚠"}.get(task.last_status, "")

def _line(task: Task) -> str:
    bits = [schedule.describe_when(task.when)]
    if task.enabled and task.next_run:
        bits.append(tr("дальше ", "next ") + time.strftime("%d.%m %H:%M", time.localtime(task.next_run)))
    elif not task.enabled:
        bits.append(tr("на паузе", "paused"))
    if task.last_run:
        bits.append(tr("было ", "last ") + ago(task.last_run) + (f" ({task.last_status})" if task.last_status else ""))
    bits.append(tr("Планировщик Windows", "Windows scheduler") if task.runner == "system" else tr("в этом окне", "this window"))
    return " · ".join(bits)

def task_option(task: Task) -> dlg.Option:
    return dlg.Option(task.title or task.prompt[:50], task, _line(task) + ("\n" + task.last_summary[:120] if task.last_summary else ""),
                      hint=_status_mark(task))

def print_list(ctx: Context) -> None:
    tasks = schedule.load_tasks()
    if not tasks:
        ctx.say(tr("Задач нет. Пример: /schedule daily 09:00 -- проверь упавшие тесты", "No tasks. Example: /schedule daily 09:00 -- check the failing tests"))
        return
    table = Table.grid(padding=(0, 2))
    for task in tasks:
        table.add_row(Text(task.id, style="dim"), Text(_status_mark(task) or " "), Text(task.title[:44], style="bold"), Text(_line(task), style="dim"))
    ctx.print(table)

async def choose_mode(ctx: Context, current: str = "acceptEdits") -> Optional[str]:
    rows = mode_rows()
    options = [dlg.Option(label, mode, hint_text, hint="✓" if mode == current else "", style="dialog.danger" if mode == "bypassPermissions" else "")
               for mode, (label, hint_text) in rows.items()]
    body = ctx.r.render(Text("  " + tr("Задача идёт без вас: если что-то требует разрешения, ей отвечается «нет» и она так и пишет в отчёте.",
                                      "The task runs without you: whatever needs permission is refused, and the report says so."), style="dim"))
    mode = await ctx.select(tr("Что задаче можно делать", "What the task may do"), options, body=body, index=list(rows).index(current) if current in rows else 1)
    if mode == "bypassPermissions" and not await ctx.confirm(
            tr("Разрешить задаче всё?", "Allow the task everything?"),
            tr("Без присмотра, без вопросов, с правами вашей учётной записи. Только для задач, которые вы проверили вручную.",
               "Unattended, with no questions, with your account's rights. Only for tasks you have tried by hand."), danger=True):
        return None
    return mode

async def ask_when(ctx: Context, default: str = "") -> Optional[dict]:
    presets = [("in 10m", tr("Через 10 минут", "In 10 minutes")), ("every 1h", tr("Каждый час", "Every hour")),
               ("daily 09:00", tr("Каждый день в 09:00", "Every day at 09:00")), ("weekdays 09:00", tr("По будням в 09:00", "Weekdays at 09:00")),
               ("", tr("Своё…", "Custom…"))]
    choice = await ctx.select(tr("Когда запускать", "When to run"), [dlg.Option(label, text) for text, label in presets])
    if choice is None:
        return None
    text = choice
    while True:
        if not text:
            text = await ctx.ask(tr("Когда? (in 20m · at 15:30 · every 2h · daily 09:00 · weekdays 09:00 · mon,fri 18:30 · cron 0 9 * * 1-5)",
                                    "When? (in 20m · at 15:30 · every 2h · daily 09:00 · weekdays 09:00 · mon,fri 18:30 · cron 0 9 * * 1-5)"), default=default)
            if not text:
                return None
        try:
            when = schedule.parse_when(text)
            break
        except ScheduleError as exc:
            ctx.say(str(exc), "warn")
            default, text = text, ""
    preview = []
    moment = time.time()
    for _ in range(3):
        following = schedule.next_run(when, moment)
        if not following:
            break
        preview.append(time.strftime("%a %d.%m %H:%M", time.localtime(following)))
        moment = following
    ctx.say(tr("Расписание: ", "Schedule: ") + schedule.describe_when(when) + (tr(" · ближайшие: ", " · next: ") + ", ".join(preview) if preview else ""))
    return when

async def new_task(ctx: Context, *, prompt: str = "", when_text: str = "") -> Optional[Task]:
    """The steps of a new task. Returns the saved task, or None when it was cancelled."""
    runtime = ctx.runtime
    if not prompt:
        prompt = await ctx.ask(tr("Что должен делать MemuCode?", "What should MemuCode do?"), body=ctx.r.render(Text("  " + tr(
            "Опишите задачу как обычно. Пример: «проверь, что тесты проходят, и если нет — коротко опиши причину».",
            "Describe it as you would normally. Example: “check that the tests pass and, if not, say briefly why”."), style="dim"))) or ""
    if not prompt.strip():
        return None
    when: Optional[dict] = None
    if when_text:
        try:
            when = schedule.parse_when(when_text)
        except ScheduleError as exc:
            ctx.say(str(exc), "warn")
    if when is None:
        when = await ask_when(ctx, when_text)
    if when is None:
        return None
    folder = str(runtime.root)
    mode = await choose_mode(ctx)
    if mode is None:
        return None
    runner = "session"
    if schedule.system_available():
        runner = await ctx.select(tr("Где запускать", "Where to run"), [
            dlg.Option(tr("В этом окне MemuCode", "In this MemuCode window"), "session",
                       tr("пока окно открыто; ничего не ставится в систему", "while the window is open; nothing is installed in the system")),
            dlg.Option(tr("В планировщике Windows", "In the Windows Task Scheduler"), "system",
                       tr("работает и при закрытом MemuCode, пока вы вошли в Windows; пропущенный запуск пройдёт при следующем включении компьютера",
                          "runs with MemuCode closed while you are signed in to Windows; a missed run starts when the computer is next awake"))]) or ""
        if not runner:
            return None
    try:
        task = schedule.new_task(prompt.strip().splitlines()[0][:60], prompt, folder, when, mode=mode, model=runtime.model, runner=runner)
    except ScheduleError as exc:
        ctx.say(str(exc), "error")
        return None
    summary = ctx.r.render(Text("\n".join([
        f"  {tr('Задача', 'Task')}: {prompt.strip()[:300]}", f"  {tr('Когда', 'When')}: {schedule.describe_when(when)}",
        f"  {tr('Папка', 'Folder')}: {folder}", f"  {tr('Модель', 'Model')}: {runtime.model or '—'}",
        f"  {tr('Права', 'Rights')}: {mode_rows()[mode][0]}",
        f"  {tr('Где', 'Where')}: " + (tr("планировщик Windows", "Windows Task Scheduler") if runner == "system" else tr("это окно", "this window"))]), style="dim"))
    if not await ctx.confirm(tr("Создать задачу?", "Create the task?"), summary):
        return None
    if runner == "system":
        ok, text = schedule.register(task)
        if not ok:
            ctx.say(tr("Не удалось добавить в планировщик Windows: ", "Could not add it to the Windows Task Scheduler: ") + text[:300], "error")
            return None
    schedule.save_task(task)
    ctx.say(tr(f"Задача {task.id} создана. Следующий запуск: ", f"Task {task.id} created. Next run: ")
            + time.strftime("%d.%m.%Y %H:%M", time.localtime(task.next_run)), "success")
    return task

async def task_menu(ctx: Context, task: Task) -> None:
    """What can be done with one task. Returns to the list when done."""
    while True:
        task = schedule.get_task(task.id) or task
        options = [dlg.Option(tr("Запустить сейчас", "Run now"), "run", tr("в фоне; результат появится здесь и в уведомлении", "in the background; the result shows here and as a notification")),
                   dlg.Option(tr("Показать последний результат", "Show the last result"), "log"),
                   dlg.Option(tr("Изменить время…", "Change the time…"), "when"),
                   dlg.Option(tr("Изменить запрос…", "Change the prompt…"), "prompt"),
                   dlg.Option(tr("Права задачи…", "What it may do…"), "mode", mode_rows().get(task.mode, ("", ""))[0]),
                   dlg.Option(tr("Включить", "Resume") if not task.enabled else tr("Поставить на паузу", "Pause"), "pause")]
        if schedule.system_available():
            options.append(dlg.Option(tr("Перенести в этом окне", "Move to this window") if task.runner == "system" else tr("Перенести в планировщик Windows", "Move to the Windows Task Scheduler"), "runner"))
        options += [dlg.Option(tr("Удалить…", "Delete…"), "delete", style="dialog.danger"), dlg.Option(tr("Назад", "Back"), None)]
        body = ctx.r.render(Text("  " + _line(task) + "\n  " + task.prompt[:400], style="dim"))
        choice = await ctx.select(f"{_status_mark(task)} {task.title}"[:100], options, body=body)
        if choice is None:
            return
        if choice == "run":
            ctx.ui.run_scheduled(task, manual=True)
            return
        if choice == "log":
            text = schedule.last_result_text(task)
            ctx.md(text or tr("Запусков ещё не было.", "It has not run yet."))
        elif choice == "when":
            when = await ask_when(ctx)
            if when is not None:
                following = schedule.next_run(when, time.time())
                schedule.update_task(task.id, when=when, next_run=following or 0.0, enabled=bool(following))
                if task.runner == "system":
                    fresh = schedule.get_task(task.id)
                    ok, text = schedule.register(fresh) if fresh else (False, "")
                    if not ok:
                        ctx.say(tr("Планировщик Windows не принял новое время: ", "The Windows scheduler did not accept the new time: ") + text[:200], "error")
        elif choice == "prompt":
            text = await ctx.ask(tr("Запрос задачи", "The task's prompt"), default=task.prompt)
            if text and text.strip():
                schedule.update_task(task.id, prompt=text.strip(), title=text.strip().splitlines()[0][:60])
        elif choice == "mode":
            mode = await choose_mode(ctx, task.mode)
            if mode:
                schedule.update_task(task.id, mode=mode)
        elif choice == "pause":
            enabled = not task.enabled
            following = schedule.next_run(task.when, time.time()) if enabled else task.next_run
            schedule.update_task(task.id, enabled=enabled, next_run=following or 0.0)
            if task.runner == "system" and schedule.system_available():
                _toggle_system(task, enabled)
        elif choice == "runner":
            target = "session" if task.runner == "system" else "system"
            if target == "system":
                ok, text = schedule.register(task)
                if not ok:
                    ctx.say(tr("Не удалось добавить в планировщик Windows: ", "Could not add it to the Windows Task Scheduler: ") + text[:300], "error")
                    continue
            else:
                schedule.unregister(task.id)
            schedule.update_task(task.id, runner=target)
        elif choice == "delete":
            if await ctx.confirm(tr(f"Удалить задачу «{task.title}»?", f"Delete the task “{task.title}”?"),
                                 tr("Её результаты тоже будут удалены.", "Its results are deleted too."), danger=True):
                await remove_task(ctx, task)
                return

def _toggle_system(task: Task, enabled: bool) -> None:
    """Pause or resume a task in the Windows scheduler: a paused one is simply removed and put back later."""
    if enabled:
        schedule.register(schedule.get_task(task.id) or task)
    else:
        schedule.unregister(task.id)

async def remove_task(ctx: Context, task: Task) -> None:
    if task.runner == "system":
        ok, text = schedule.unregister(task.id)
        if not ok:
            ctx.say(tr("Задача не снялась с планировщика Windows: ", "The task could not be removed from the Windows scheduler: ") + text[:300], "warn")
    schedule.delete_task(task.id)
    import shutil
    shutil.rmtree(schedule.home() / "results" / task.id, ignore_errors=True)
    ctx.say(tr("Задача удалена", "Task deleted"), "success")

async def manage(ctx: Context) -> None:
    while True:
        tasks = schedule.load_tasks()
        options: List[dlg.Option] = [dlg.Option(tr("＋ Новая задача…", "＋ New task…"), "__new__")]
        options += [task_option(task) for task in tasks]
        options.append(dlg.Option(tr("Готово", "Done"), None))
        body = ctx.r.render(Text("  " + (tr("Задач: ", "Tasks: ") + str(len(tasks)) if tasks else
                                          tr("Задач пока нет: MemuCode может сам выполнять запрос по расписанию.", "No tasks yet: MemuCode can run a prompt by itself on a schedule.")), style="dim"))
        choice = await ctx.select(tr("Задачи по расписанию", "Scheduled tasks"), options, body=body, filterable=len(options) > 12)
        if choice is None:
            return
        if choice == "__new__":
            await new_task(ctx)
        else:
            await task_menu(ctx, choice)

@command("schedule", "Задачи по расписанию: запрос, который MemuCode выполняет сам (в этом окне или планировщиком Windows)",
         "Scheduled tasks: a prompt MemuCode runs by itself (in this window or by the Windows scheduler)",
         hint="[when -- prompt | list | run|pause|resume|delete|log <id>]", category="agents", while_busy=True, immediate=True)
async def schedule_command(ctx: Context) -> None:
    args = ctx.args.strip()
    if not args:
        await manage(ctx)
        return
    word, _, rest = args.partition(" ")
    low = word.lower()
    if low in ("list", "ls", "список"):
        print_list(ctx)
        return
    if low in ("run", "pause", "resume", "delete", "log", "rm", "запустить", "пауза", "удалить"):
        task = schedule.get_task(rest.strip())
        if task is None:
            ctx.say(tr(f"Нет задачи «{rest.strip()}». Список: /schedule list", f"No task “{rest.strip()}”. List: /schedule list"), "warn")
            return
        if low in ("run", "запустить"):
            ctx.ui.run_scheduled(task, manual=True)
        elif low == "log":
            ctx.md(schedule.last_result_text(task) or tr("Запусков ещё не было.", "It has not run yet."))
        elif low in ("pause", "пауза"):
            schedule.update_task(task.id, enabled=False)
            if task.runner == "system":
                _toggle_system(task, False)
            ctx.say(tr("На паузе", "Paused"), "success")
        elif low == "resume":
            following = schedule.next_run(task.when, time.time())
            schedule.update_task(task.id, enabled=True, next_run=following or 0.0)
            if task.runner == "system":
                _toggle_system(task, True)
            ctx.say(tr("Включена", "Resumed"), "success")
        else:
            if await ctx.confirm(tr(f"Удалить «{task.title}»?", f"Delete “{task.title}”?"), danger=True):
                await remove_task(ctx, task)
        return
    if " -- " in args or args.startswith("-- "):
        when_text, _, prompt = args.partition("--")
        await new_task(ctx, prompt=prompt.strip(), when_text=when_text.strip())
        return
    ctx.say(tr("Форма: /schedule <когда> -- <запрос>, например /schedule daily 09:00 -- проверь тесты. Без аргументов — меню.",
               "Form: /schedule <when> -- <prompt>, for example /schedule daily 09:00 -- check the tests. With no arguments — the menu."), "warn")
