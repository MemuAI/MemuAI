"""Work commands: /init /review /security-review /test /fix /explain /refactor
/commit /pr /diff /plan /goal /btw /background /browser /computer /search
/fetch /image /paste /voice /decide /loop /run /add."""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Dict

from rich.panel import Panel
from rich.text import Text

from ..i18n import tr
from ..session import new_id
from ..tui import dialogs as dlg
from . import Context, command


@command("attach", "Приложить файл", "Attach an image or text file", hint="<path>", category="code", while_busy=True)
async def attach(ctx: Context) -> None:
    name = ctx.args.strip()
    if not name:
        name = await ctx.ask("File to attach", body="Enter or drag a file path") or ""
    if name:
        ctx.ui.attach_file(name)

INIT_PROMPT = """Analyze this codebase and create a MEMUCODE.md file in the project root. It will be given to future coding-agent sessions working in this repository.

Include:
1. The commands to build, lint, type-check and test — including how to run a single test.
2. A high-level architecture overview: the big picture that needs several files to understand (entry points, main modules and how they talk, data flow, important conventions). Do not list every file.
3. Project-specific conventions and gotchas a newcomer would get wrong.

Rules: read the real files first (README, manifests, CI config, existing AGENTS.md/CLAUDE.md/.cursorrules/.github/copilot-instructions.md — merge their important parts). Do not invent anything you did not see. Keep it concise and practical, no generic advice ("write tests", "handle errors"). If MEMUCODE.md already exists, improve it instead of replacing good content. Start the file with:

# MEMUCODE.md

This file guides MemuCode (and other coding agents) when working with code in this repository."""

REVIEW_PROMPT = """Review the current changes in this repository{target}. Do not modify any files.

1. Look at `git status` and the diff (staged and unstaged; new files too). If there are no local changes, review the last commit.
2. Read enough surrounding code to judge each change in context.
3. Report findings ordered by severity — bugs and regressions first, then risky edge cases, security, performance, then readability. For each: `path:line`, what is wrong, why it matters, and a concrete fix. Distinguish facts from suspicions.
4. Finish with a one-paragraph verdict. If the change looks correct, say so plainly — do not invent problems."""

SECURITY_PROMPT = """Perform a security review of the pending changes in this repository (staged, unstaged and new files; if none, the last commit). Do not modify files.

Look for: injection (SQL, command, template, path traversal), unsafe deserialization, SSRF, XSS/CSRF, authentication and authorization flaws, secrets or credentials in code or logs, unsafe crypto, dangerous defaults, dependency risks and data exposure. For each real finding give severity (critical/high/medium/low), `path:line`, the exploit scenario, and the fix. Only report issues with a plausible attack path; say explicitly when nothing significant is found."""

TEST_PROMPT = """Run the project's tests{target}.

Find the correct test command (package manifests, CI config, MEMUCODE.md). Run it. For each failure: show the failing test, the assertion or error, locate the root cause in the code, and say whether the test or the code is wrong. Fix clear bugs with minimal changes and re-run to prove the fix; for anything ambiguous, explain and ask before changing behaviour."""

COMMIT_PROMPT = """Create a git commit for the current changes.

1. Run `git status` and `git diff` (and `git diff --staged`) to see exactly what changed; check `git log -5 --oneline` for the repository's message style.
2. Stage only files that belong to this change (never secrets, build output or unrelated edits — ask if unsure).
3. Write a concise message in the repository's style: a summary line (imperative, ≤ 72 chars), a blank line, and a short body explaining why when useful.{coauthor}
4. Commit, then show `git log -1 --stat`. Do not push.{extra}"""

PR_PROMPT = """Prepare a pull request for the current branch.

1. Check the branch, `git status`, and the commits and diff against the main branch.
2. If there are uncommitted changes, ask whether to commit them first.
3. Push the branch (ask before pushing) and create the PR with `gh pr create` (title ≤ 70 chars; body: summary, changes, how it was tested). If `gh` is not installed or not authenticated, output the title and body for the user to paste instead.{extra}"""


def _coauthor(ctx: Context) -> str:
    if ctx.settings.get("include_co_authored_by", True):
        return "\n   End the message with the line: Co-Authored-By: MemuCode <noreply@memuai.dev>"
    return ""


@command("init", "Изучить проект и создать MEMUCODE.md с инструкциями для агента", "Study the project and write MEMUCODE.md for agents",
         category="code", while_busy=False)
async def init(ctx: Context) -> None:
    extra = f"\n\nAdditional wishes of the user: {ctx.args}" if ctx.args else ""
    await ctx.prompt(INIT_PROMPT + extra)
    ctx.runtime.invalidate_prompt(memory=True)


@command("review", "Ревью текущих изменений (без правок)", "Review the current changes (no edits)", hint="[target]", category="code", while_busy=False)
async def review(ctx: Context) -> None:
    target = f" — focus: {ctx.args}" if ctx.args else ""
    await ctx.prompt(REVIEW_PROMPT.format(target=target))


@command("security-review", "Проверка изменений на уязвимости", "Security review of the pending changes", category="code", while_busy=False)
async def security_review(ctx: Context) -> None:
    await ctx.prompt(SECURITY_PROMPT + (f"\n\nFocus: {ctx.args}" if ctx.args else ""))


@command("test", "Запустить тесты и разобрать падения", "Run the tests and triage failures", hint="[target]", category="code", while_busy=False)
async def test(ctx: Context) -> None:
    await ctx.prompt(TEST_PROMPT.format(target=f" ({ctx.args})" if ctx.args else ""))


@command("fix", "Воспроизвести ошибку, найти причину и исправить", "Reproduce a bug, find the cause, fix it", hint="<description>",
         category="code", while_busy=False)
async def fix(ctx: Context) -> None:
    problem = ctx.args or await ctx.ask(tr("Что сломано?", "What is broken?"))
    if not problem:
        return
    await ctx.prompt(f"Fix this problem: {problem}\n\nFirst reproduce or locate it and explain the root cause in one "
                     "short paragraph; then make the smallest correct change and verify it (run the relevant tests "
                     "or a reproduction).")


@command("explain", "Объяснить код, файл или механизм", "Explain code, a file or a mechanism", hint="<target>", category="code", while_busy=False)
async def explain(ctx: Context) -> None:
    target = ctx.args or await ctx.ask(tr("Что объяснить? (файл, функция, @путь)", "What should I explain? (file, function, @path)"))
    if not target:
        return
    await ctx.prompt(f"Explain {target}. Read the relevant code first. Explain what it does, how it works step by step, "
                     "how it connects to the rest of the project, and any non-obvious details. Refer to code as "
                     "path:line. Do not change anything.")


@command("refactor", "Улучшить код, сохранив поведение", "Refactor while preserving behaviour", hint="<target>", category="code", while_busy=False)
async def refactor(ctx: Context) -> None:
    target = ctx.args or await ctx.ask(tr("Что отрефакторить?", "What should be refactored?"))
    if not target:
        return
    await ctx.prompt(f"Refactor {target} without changing its observable behaviour. Read the code and its callers first, "
                     "keep the project's style, make the change in small reviewable steps, and run the tests after.")


@command("commit", "Составить сообщение и сделать git-коммит", "Write the message and make a git commit", hint="[instructions]",
         category="code", while_busy=False)
async def commit(ctx: Context) -> None:
    extra = f"\n\nThe user added: {ctx.args}" if ctx.args else ""
    await ctx.prompt(COMMIT_PROMPT.format(coauthor=_coauthor(ctx), extra=extra))


@command("pr", "Подготовить и открыть pull request", "Prepare and open a pull request", hint="[instructions]",
         aliases=("pull-request",), category="code", while_busy=False)
async def pr(ctx: Context) -> None:
    extra = f"\n\nThe user added: {ctx.args}" if ctx.args else ""
    await ctx.prompt(PR_PROMPT.format(extra=extra))


@command("diff", "Показать незакоммиченные изменения и правки этой сессии", "Show uncommitted changes and this session's edits",
         hint="[path]", category="code", while_busy=True)
async def diff(ctx: Context) -> None:
    from ... import code_checkpoints, code_tools
    from ...work_tools import WorkError
    runtime = ctx.runtime
    try:
        data = await asyncio.to_thread(code_tools.tool_git, runtime.root, {"action": "diff", "path": ctx.args.strip()})
    except WorkError as exc:
        data = {"repository": False, "error": str(exc)}
    if data.get("error"):
        ctx.say(tr(f"git: {data['error']}", f"git failed: {data['error']}"), "warn")
    if data.get("repository") and data.get("diff", "").strip():
        text = data["diff"]
        for block in re.split(r"(?=^diff --git )", text, flags=re.M):
            if not block.strip():
                continue
            match = re.match(r"diff --git a/(\S+)", block)
            title = match.group(1) if match else ""
            ctx.ui.write(ctx.r.diff_panel(block, title=title, max_lines=None if ctx.ui.verbose else 80))
    elif data.get("repository"):
        ctx.say(tr("Незакоммиченных изменений нет", "No uncommitted changes"))
    entries = code_checkpoints.list_entries(runtime.session.id)
    if entries:
        ctx.print(Text(tr("Файлы, изменённые в этой сессии (откат: /undo, /rewind):", "Files changed in this session (undo: /undo, /rewind):"),
                       style="bold"), *[Text(f"  {'M' if row['existed'] else 'A'} {row['path']}", style="dim") for row in entries[:40]])
    elif not data.get("repository") and not data.get("error"):
        # code_tools' note is Russian (it is written for the model); the CLI speaks English.
        ctx.say(tr("Это не git-репозиторий, и в сессии ещё нет правок", "Not a git repository and no edits yet"))


@command("plan", "Режим плана: сначала исследовать и согласовать план", "Plan mode: research and agree on a plan first",
         hint="[task]", category="code", while_busy=False)
async def plan(ctx: Context) -> None:
    runtime = ctx.runtime
    if ctx.args.strip() in ("off", "выкл"):
        runtime.permissions.set_mode(runtime.permissions.shared_mode(), source="plan")
        ctx.say(tr("Режим плана выключен", "Plan mode is off"), "success")
        return
    runtime.permissions.set_mode("plan", source="plan")
    if ctx.args.strip():
        await ctx.prompt(ctx.args.strip(), display=ctx.raw)
    else:
        ctx.say(tr("Режим плана: агент будет только исследовать и предложит план. Опишите задачу.",
                   "Plan mode: the agent only researches and proposes a plan. Describe the task."), "success")


@command("goal", "Цель: агент работает шаг за шагом, пока не достигнет её", "Goal: the agent keeps working until it is met",
         hint="<goal>|status|pause|continue|clear", category="code", while_busy=False)
async def goal(ctx: Context) -> None:
    runtime = ctx.runtime
    session = runtime.session
    args = ctx.args.strip()
    current = session.meta.get("goal") if isinstance(session.meta.get("goal"), dict) else None
    if args in ("", "status", "статус"):
        if not current or not current.get("text"):
            ctx.say(tr("Цель не задана. Пример: /goal все тесты зелёные и CI проходит",
                       "No goal. Example: /goal all tests pass and CI is green"))
            return
        state = tr("достигнута", "achieved") if current.get("done") else (tr("на паузе", "paused") if current.get("paused") else tr("в работе", "active"))
        ctx.md(f"**◎ {current['text']}**\n\n{state} · {current.get('continuations', 0)}/{current.get('max', 10)}")
        return
    if args in ("clear", "off", "сброс"):
        session.set_meta(goal=None)
        ctx.say(tr("Цель снята", "Goal cleared"), "success")
        return
    if args in ("pause", "пауза") and current:
        current["paused"] = True
        session.set_meta(goal=current)
        ctx.say(tr("Цель на паузе", "Goal paused"), "success")
        return
    if args.split()[0] in ("continue", "продолжить") and current:
        extra = args.split()[1] if len(args.split()) > 1 and args.split()[1].isdigit() else "5"
        current.update({"paused": False, "done": False, "max": int(current.get("continuations", 0)) + int(extra)})
        session.set_meta(goal=current)
        await ctx.prompt("Continue working toward the goal: " + current["text"], display="↻ goal")
        return
    limit = 10
    match = re.search(r"--max[= ](\d+)", args)
    if match:
        limit = int(match.group(1))
        args = args.replace(match.group(0), "").strip()
    session.set_meta(goal={"text": args, "max": limit, "continuations": 0, "done": False, "paused": False,
                           "created": time.time()})
    ctx.say(tr(f"◎ Цель: {args} (до {limit} продолжений)", f"◎ Goal: {args} (up to {limit} continuations)"), "success")
    await ctx.prompt(f"New persistent goal: {args}\n\nPlan the work, then execute it in verified increments. "
                     "Report `GOAL COMPLETE: <evidence>` only when the goal is fully met and verified.", display=ctx.raw)


@command("btw", "Спросить по ходу дела, не прерывая работу агента", "Ask a side question without interrupting the agent",
         hint="<question>", category="general", while_busy=True)
async def btw(ctx: Context) -> None:
    question = ctx.args.strip()
    if not question:
        ctx.say(tr("Пример: /btw какая версия Python у проекта?", "Example: /btw which Python version does the project use?"))
        return
    runtime = ctx.runtime
    context = runtime.session.brief_context(12000)
    system = ("You answer a quick side question from a developer while their coding agent works. Use the conversation "
              "excerpt for context. Answer briefly and precisely in the user's language; say so if the excerpt does not "
              "contain the answer. You cannot use tools.")
    try:
        answer = await runtime.quick_answer(system, f"<conversation>\n{context}\n</conversation>\n\nQuestion: {question}")
    except Exception as exc:
        ctx.say(str(exc), "error")
        return
    ctx.print(Panel(ctx.r.md(answer), title=f"btw · {question[:60]}", title_align="left", border_style="dim"))


@command("background", "Запустить задачу параллельно (или &задача)", "Run a task in the background (or &task)", hint="<task>",
         aliases=("bg", "subtask"), category="agents", while_busy=True)
async def background(ctx: Context) -> None:
    task = ctx.args.strip() or await ctx.ask(tr("Что сделать в фоне?", "What should run in the background?"))
    if not task:
        return
    job = ctx.runtime.jobs.start(task)
    ctx.say(tr(f"Фоновая задача #{job['id']} запущена. Ход: /tasks {job['id']}",
               f"Background task #{job['id']} started. Progress: /tasks {job['id']}"), "success")


async def _direct_tool(ctx: Context, name: str, args: Dict[str, Any]) -> None:
    """Run one tool now, with the usual permissions and display, and record it in the conversation."""
    from ..agent import Agent
    agent = Agent(ctx.runtime)
    turn = new_id("turn")
    ctx.runtime.session.append({"role": "user", "kind": "command", "content": f"[The user ran {name} directly: "
                                f"{json.dumps(args, ensure_ascii=False)[:600]}]", "display": ctx.raw, "turn": turn})
    await agent.execute([{"id": new_id("t"), "name": name, "args": args}], turn)


@command("browser", "Поручить задачу браузерному агенту (изолированный Edge)", "Give a task to the browser agent (isolated Edge)",
         hint="<task>", category="agents", while_busy=False)
async def browser(ctx: Context) -> None:
    task = ctx.args.strip() or await ctx.ask(tr("Что сделать в браузере?", "What should the browser do?"))
    if task:
        url = re.search(r"https?://\S+", task)
        await _direct_tool(ctx, "Browser", {"task": task, **({"url": url.group(0)} if url else {})})


@command("computer", "Computer Use: управлять рабочим столом Windows (Esc — стоп)", "Computer Use: operate the Windows desktop (Esc stops)",
         hint="<task>", category="agents", while_busy=False)
async def computer(ctx: Context) -> None:
    task = ctx.args.strip() or await ctx.ask(tr("Что сделать на компьютере?", "What should be done on the computer?"))
    if task:
        await _direct_tool(ctx, "Computer", {"task": task})


@command("search", "Поиск в интернете", "Search the web", hint="<query>", category="agents", while_busy=False)
async def search(ctx: Context) -> None:
    query = ctx.args.strip() or await ctx.ask(tr("Что найти?", "Search for?"))
    if query:
        await _direct_tool(ctx, "WebSearch", {"query": query, "limit": 8})


@command("fetch", "Прочитать веб-страницу", "Read a web page", hint="<url> [what to find]", category="agents", while_busy=False)
async def fetch(ctx: Context) -> None:
    words = ctx.args.split(maxsplit=1)
    if not words:
        ctx.say(tr("Пример: /fetch https://docs.python.org/3/whatsnew/ что нового в asyncio", "Example: /fetch https://… what changed"))
        return
    args: Dict[str, Any] = {"url": words[0]}
    if len(words) > 1:
        args["prompt"] = words[1]
    await _direct_tool(ctx, "WebFetch", args)


@command("image", "Приложить картинку из файла", "Attach an image file", hint="<path>", category="context", while_busy=True)
async def image(ctx: Context) -> None:
    from ..tui.input import image_from_path
    path = ctx.args.strip().strip('"') or await ctx.ask(tr("Путь к изображению", "Image path"))
    if not path:
        return
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = ctx.runtime.root / target
    found = image_from_path(str(target))
    if not found:
        ctx.say(tr("Не удалось открыть изображение", "Could not open the image"), "error")
        return
    ctx.ui.input_buffer.insert_text(ctx.ui.attachments.image_token(found) + " ")


@command("paste", "Вставить картинку из буфера обмена (Alt+V)", "Paste an image from the clipboard (Alt+V)", category="context",
         while_busy=True)
async def paste(ctx: Context) -> None:
    await ctx.ui.paste_image()


@command("voice", "Надиктовать сообщение голосом", "Dictate a message", category="general", while_busy=True)
async def voice(ctx: Context) -> None:
    from ..voice import Recorder, mci_available, transcribe
    if not mci_available():
        ctx.say(tr("Микрофон не найден (голосовой ввод работает в Windows)", "No microphone found (voice input works on Windows)"), "error")
        return
    recorder = Recorder()
    try:
        recorder.start()
    except Exception as exc:
        ctx.say(tr(f"Запись не началась: {exc}", f"Recording did not start: {exc}"), "error")
        return
    dialog = dlg.Dialog(title=tr("● Запись", "● Recording"), options=[
        dlg.Option(tr("Готово — распознать", "Done — transcribe"), "done"), dlg.Option(tr("Отмена", "Cancel"), None)])

    async def tick() -> None:
        while True:
            seconds = int(recorder.seconds)
            dialog.body = ctx.r.render(Text(tr(f"  ●  говорите… {seconds // 60:02d}:{seconds % 60:02d}  (Enter — стоп)",
                                               f"  ●  speak… {seconds // 60:02d}:{seconds % 60:02d}  (Enter to stop)"), style="err"))
            ctx.ui.invalidate()
            await asyncio.sleep(0.5)

    ticker = asyncio.ensure_future(tick())
    try:
        choice = await ctx.ui.open_dialog(dialog)
    finally:
        ticker.cancel()
    if choice != "done":
        recorder.cancel()
        return
    audio = await asyncio.to_thread(recorder.stop)
    ctx.ui.status_text = tr("распознаю речь", "transcribing")
    try:
        text = await transcribe(audio, model=ctx.runtime.small_model())
    except Exception as exc:
        ctx.say(tr(f"Не удалось распознать: {exc}", f"Transcription failed: {exc}"), "error")
        return
    finally:
        ctx.ui.status_text = ""
    if text:
        ctx.ui.input_buffer.insert_text(text)
    else:
        ctx.say(tr("Речь не распознана", "No speech recognised"), "warn")


@command("decide", "Спросить модель решений Jev: /decide «текст» ? вопрос [| вариант | вариант]",
         "Ask the Jev decision model: /decide \"text\" ? question [| option | option]", hint="<text> ? <question>",
         category="agents", while_busy=True)
async def decide(ctx: Context) -> None:
    from ...decisions import Question
    client = ctx.runtime.decisions
    if not client.enabled:
        ctx.say(tr("Модель решений не подключена: /decisions", "No decision model connected: /decisions"), "warn")
        return
    raw = ctx.args
    if "?" not in raw:
        ctx.say(tr('Формат: /decide текст ? вопрос   или   /decide текст ? вопрос | вариант1 | вариант2',
                   'Format: /decide text ? question   or   /decide text ? question | option1 | option2'), "warn")
        return
    state, question = raw.split("?", 1)
    state = state.strip().strip('"«»')
    options = [part.strip() for part in question.split("|")]
    question_text = options.pop(0)
    if not state:
        state = ctx.runtime.session.last_assistant_text()[-6000:]
    try:
        q = Question.choice(question_text, options) if len(options) >= 2 else Question.noul(question_text)
        decision = await client.ask(state, {"q": q})
    except Exception as exc:
        ctx.say(str(exc), "error")
        return
    answer = decision["q"]
    ctx.runtime.stats.add_decision(decision)
    if answer.type == "choice":
        lines = [Text(f"  → {answer.choice}  ({answer.certainty:.0%})", style="bold brand")]
        for option, probability in sorted(answer.probabilities.items(), key=lambda item: -item[1]):
            line = Text(f"    {option[:30]:<30} ")
            line.append_text(ctx.r.meter(probability, 20))
            line.append(f" {probability:.0%}", style="dim")
            lines.append(line)
        ctx.print(*lines, Text(f"  {decision.latency_ms} ms · {decision.model}", style="subtle"))
    else:
        line = Text(f"  {question_text[:60]}  ")
        line.append_text(ctx.r.meter(float(answer.noul or 0), 20))
        line.append(f" {float(answer.noul or 0):.0%}", style="bold")
        ctx.print(line, Text(f"  {decision.latency_ms} ms · {decision.model}", style="subtle"))


@command("loop", "Повторять запрос по расписанию: /loop 10m проверь CI · /loop stop", "Repeat a prompt: /loop 10m check CI · /loop stop",
         hint="<interval> <query>|stop", category="agents", while_busy=True)
async def loop(ctx: Context) -> None:
    ui = ctx.ui
    args = ctx.args.strip()
    existing = getattr(ui, "loop_task", None)
    if args in ("stop", "off", "стоп"):
        if existing and not existing.done():
            existing.cancel()
            ctx.say(tr("Повтор остановлен", "Loop stopped"), "success")
        else:
            ctx.say(tr("Повтора нет", "No loop is running"))
        return
    match = re.match(r"^(\d+)\s*(s|m|h|с|м|ч)?\s+(.+)$", args, re.S)
    if not match:
        ctx.say(tr("Формат: /loop 5m <запрос>  (s, m, h)", "Format: /loop 5m <prompt>  (s, m, h)"), "warn")
        return
    number, unit, prompt = int(match.group(1)), (match.group(2) or "m"), match.group(3).strip()
    seconds = number * {"s": 1, "с": 1, "m": 60, "м": 60, "h": 3600, "ч": 3600}[unit]
    seconds = max(30, seconds)
    if existing and not existing.done():
        existing.cancel()

    async def repeat() -> None:
        runs = 0
        while runs < 200:
            while ui.busy:
                await asyncio.sleep(2)
            runs += 1
            ui.send_prompt(prompt, display=f"↻ {prompt}")
            await asyncio.sleep(seconds)

    ui.loop_task = asyncio.ensure_future(repeat())
    ctx.say(tr(f"Каждые {number}{unit}: «{prompt[:60]}» · /loop stop — остановить",
               f"Every {number}{unit}: “{prompt[:60]}” · /loop stop to end"), "success")


@command("run", "Выполнить команду в терминале (как !команда)", "Run a shell command (like !command)", hint="<command>",
         category="code", while_busy=False)
async def run(ctx: Context) -> None:
    if ctx.args.strip():
        await ctx.ui.run_bash(ctx.args.strip())


@command("add", "Приложить файлы к следующему сообщению", "Attach files to the next message", hint="<files>",
         category="context", while_busy=True)
async def add(ctx: Context) -> None:
    for item in ctx.words():
        token = f'@"{item}"' if " " in item else "@" + item
        ctx.ui.input_buffer.insert_text(token + " ")
