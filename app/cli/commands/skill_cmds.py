"""``/skills`` — browse, create, edit, delete, disable and restore skills, all inside the terminal.

Nothing here hands the screen to another program: a skill is written in the interface's own text field
(Ctrl+S saves), or drafted by the model from a plain description and then edited the same way.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

from rich.text import Text

from ..i18n import tr
from .. import skillkit
from ..llm import LLMError
from ..tui import dialogs as dlg
from . import Context, command

DRAFT_SYSTEM = (
    "You write one MemuCode skill: a SKILL.md playbook that the coding agent loads when a task matches it. Reply with "
    "ONLY the file content — no code fences, no commentary.\n"
    "Format: a header between two --- lines with exactly two keys, `name: {slug}` and `description: <ONE sentence "
    "saying what the skill does and WHEN to use it>`; then Markdown instructions: a short title, when to use it, "
    "numbered steps the agent follows, the checks that prove the work is done and the format of the final report.\n"
    "Be concrete and short (under 60 lines). Name real commands, files or tools only if the user's description gives "
    "them — never invent any. Write in the language of the user's description.")

def _row(ctx: Context, name: str) -> Optional[Dict[str, Any]]:
    return ctx.runtime.skills.get(name, include_disabled=True)

def _refresh(ctx: Context) -> None:
    ctx.runtime.invalidate_prompt(memory=True)
    ctx.runtime.skills.catalogue(refresh=True, include_disabled=True)

def _strip_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```[\w-]*\s*\n(.*?)\n```\s*$", text, re.S)
    return match.group(1).strip() if match else text

async def edit_text(ctx: Context, title: str, text: str, *, note: str = "") -> Optional[str]:
    """Edit a skill in the interface's own multi-line field. None when cancelled (Esc)."""
    body = ctx.r.render(Text("  " + (note or tr("Ctrl+S — сохранить, Enter — новая строка, Esc — отмена",
                                                "Ctrl+S saves, Enter starts a new line, Esc cancels")), style="dim"))
    return await ctx.ui.prompt_text(title, body=body, default=text, tag="paste")

async def save_checked(ctx: Context, scope: str, slug: str, text: str, *, overwrite: bool) -> Optional[Path]:
    """Check the text, let the user fix what is wrong, write it. None when the user gave up."""
    while True:
        problems = skillkit.check_text(skillkit.normalize(text, slug))
        if not problems:
            break
        ctx.say(" · ".join(problems), "warn")
        again = await edit_text(ctx, tr(f"Скилл {slug}: исправьте", f"Skill {slug}: fix it"), text)
        if again is None:
            return None
        text = again
    try:
        return skillkit.write_skill(ctx.runtime.root, scope, slug, text, overwrite=overwrite)
    except FileExistsError:
        ctx.say(tr("Такой скилл уже есть", "That skill already exists"), "error")
    except (OSError, ValueError) as exc:
        ctx.say(str(exc), "error")
    return None

async def draft_with_model(ctx: Context, slug: str, wish: str) -> str:
    """The model's first draft of a skill, from the user's plain description."""
    runtime = ctx.runtime
    ctx.ui.status_text = tr("MemuCode пишет скилл", "MemuCode is drafting the skill")
    try:
        text = await runtime.quick_answer(DRAFT_SYSTEM.replace("{slug}", slug), wish, model=runtime.model, max_tokens=3500)
    except (LLMError, Exception) as exc:                      # noqa: BLE001 — any failure falls back to the template
        ctx.say(tr(f"Модель не смогла написать черновик: {exc}", f"The model could not draft it: {exc}"), "warn")
        return ""
    finally:
        ctx.ui.status_text = ""
    return skillkit.normalize(_strip_fences(text), slug, wish.splitlines()[0][:160])

async def create_skill(ctx: Context, name: str = "") -> None:
    runtime = ctx.runtime
    name = name or await ctx.ask(tr("Как назвать скилл? (коротко, латиницей или кириллицей, например release-notes)",
                                    "Name of the skill — short, e.g. release-notes")) or ""
    slug = skillkit.slugify(name)
    problem = skillkit.name_problem(slug)
    if problem:
        ctx.say(problem, "warn")
        return
    scope = await ctx.select(tr("Где хранить скилл?", "Where should the skill live?"), [
        dlg.Option(tr("В этом проекте", "In this project"), "project",
                   tr(f".memucode/skills/{slug} — вместе с репозиторием; работает в доверенной папке",
                      f".memucode/skills/{slug} — travels with the repository; works in a trusted folder")),
        dlg.Option(tr("Личный", "Personal"), "personal",
                   tr("для всех ваших проектов", "for all your projects"))],
        index=0 if runtime.trusted() else 1)
    if not scope:
        return
    existing = skillkit.skill_dir(runtime.root, scope, slug) / "SKILL.md"
    overwrite = False
    if existing.exists():
        if not await ctx.confirm(tr(f"Скилл «{slug}» уже есть. Заменить?", f"Skill “{slug}” already exists. Replace it?"),
                                 str(existing), danger=True):
            return
        overwrite = True
    how = await ctx.select(tr("Как создать?", "How do you want to write it?"), [
        dlg.Option(tr("Опишу словами — MemuCode напишет", "Describe it — MemuCode writes it"), "ai",
                   tr("модель составит черновик, вы поправите его здесь же", "the model drafts it, you edit it right here")),
        dlg.Option(tr("Напишу сам в терминале", "Write it myself, here in the terminal"), "own",
                   tr("откроется поле ввода с шаблоном", "a text field opens with a template")),
        dlg.Option(tr("Отмена", "Cancel"), None)])
    if not how:
        return
    text = skillkit.template(slug)
    if how == "ai":
        wish = await ctx.ask(tr("Что должен делать скилл и когда его применять?", "What should the skill do, and when should it be used?"),
                             body=ctx.r.render(Text("  " + tr("Пара предложений достаточно: задача, шаги, чем проверять результат.",
                                                              "A few sentences are enough: the job, the steps, how to check the result."),
                                                    style="dim")))
        if not wish:
            return
        drafted = await draft_with_model(ctx, slug, wish)
        if drafted:
            text = drafted
    edited = await edit_text(ctx, tr(f"Скилл {slug}", f"Skill {slug}"), text)
    if edited is None:
        ctx.say(tr("Создание отменено", "Cancelled — nothing was saved"))
        return
    written = await save_checked(ctx, scope, slug, edited, overwrite=overwrite)
    if written is None:
        return
    _refresh(ctx)
    ctx.say(tr(f"Скилл «{slug}» создан: {written}. Запуск: /{slug} <задача>", f"Created skill “{slug}”: {written}. Run it with /{slug} <task>"),
            "success")

async def edit_skill(ctx: Context, row: Dict[str, Any]) -> None:
    slug = row["id"]
    if row.get("builtin") or not row.get("path"):
        scope = await ctx.select(tr("Встроенный скилл нельзя править — сделать свою копию?", "A built-in skill cannot be edited — make your own copy?"), [
            dlg.Option(tr("Личная копия", "A personal copy"), "personal", tr("заменит встроенный у вас везде", "replaces the built-in one for you everywhere")),
            dlg.Option(tr("Копия в проекте", "A copy in this project"), "project"),
            dlg.Option(tr("Отмена", "Cancel"), None)])
        if not scope:
            return
        text = skillkit.normalize(f"---\nname: {slug}\ndescription: {row.get('description') or row.get('title') or slug}\n---\n\n"
                                  f"# {row.get('title') or slug}\n\n{row.get('prompt') or ''}\n", slug)
        edited = await edit_text(ctx, tr(f"Скилл {slug} (копия)", f"Skill {slug} (copy)"), text)
        if edited is None:
            return
        written = await save_checked(ctx, scope, slug, edited, overwrite=True)
        if written:
            _refresh(ctx)
            ctx.say(tr(f"Сохранено: {written}", f"Saved: {written}"), "success")
        return
    path = Path(row["path"])
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        ctx.say(str(exc), "error")
        return
    edited = await edit_text(ctx, tr(f"Скилл {slug}", f"Skill {slug}"), text, note=str(path))
    if edited is None or edited == text:
        return
    problems = skillkit.check_text(skillkit.normalize(edited, slug))
    while problems:
        ctx.say(" · ".join(problems), "warn")
        edited = await edit_text(ctx, tr(f"Скилл {slug}: исправьте", f"Skill {slug}: fix it"), edited)
        if edited is None:
            return
        problems = skillkit.check_text(skillkit.normalize(edited, slug))
    path.write_text(skillkit.normalize(edited, slug), encoding="utf-8")
    _refresh(ctx)
    ctx.say(tr(f"Скилл «{slug}» сохранён", f"Skill “{slug}” saved"), "success")

async def delete_skill(ctx: Context, row: Dict[str, Any]) -> None:
    slug = row["id"]
    if row.get("builtin") or not row.get("path"):
        ctx.say(tr(f"«{slug}» встроенный: удалить нельзя, но можно выключить (/skills disable {slug}).",
                   f"“{slug}” is built in: it cannot be deleted, but it can be disabled (/skills disable {slug})."), "warn")
        return
    if not await ctx.confirm(tr(f"Удалить скилл «{slug}»?", f"Delete the skill “{slug}”?"),
                             tr("Папка уйдёт в корзину — вернуть можно через /skills restore.\n",
                                "The folder goes to the trash — bring it back with /skills restore.\n") + str(row["path"]),
                             danger=True):
        return
    try:
        moved = skillkit.delete_skill(ctx.runtime.root, Path(row["path"]))
    except (OSError, ValueError) as exc:
        ctx.say(str(exc), "error")
        return
    _refresh(ctx)
    ctx.say(tr(f"Скилл «{slug}» удалён (корзина: {moved})", f"Deleted “{slug}” (trash: {moved})"), "success")

async def toggle_skill(ctx: Context, row: Dict[str, Any], enable: Optional[bool] = None) -> None:
    slug = row["id"]
    enable = (not row.get("enabled", True)) if enable is None else enable
    skillkit.set_disabled(ctx.settings, slug, not enable)
    _refresh(ctx)
    ctx.say(tr(f"Скилл «{slug}»: {'включён' if enable else 'выключен'}", f"Skill “{slug}”: {'enabled' if enable else 'disabled'}"), "success")

async def restore_skill(ctx: Context) -> None:
    rows = skillkit.list_trash()
    if not rows:
        ctx.say(tr("Корзина скиллов пуста", "The skills trash is empty"))
        return
    entry = await ctx.select(tr("Какой скилл вернуть?", "Restore which skill?"),
                             [dlg.Option(row["id"], row, str(row["description"])[:100]) for row in rows], filterable=len(rows) > 12)
    if not entry:
        return
    scope = await ctx.select(tr("Куда вернуть?", "Restore it to…"), [
        dlg.Option(tr("В этот проект", "This project"), "project"), dlg.Option(tr("В личные", "Personal"), "personal")])
    if not scope:
        return
    try:
        restored = skillkit.restore_skill(ctx.runtime.root, entry, scope)
    except FileExistsError:
        ctx.say(tr("Скилл с таким именем уже есть — сначала удалите или переименуйте его", "A skill with that name exists — remove or rename it first"), "error")
        return
    except OSError as exc:
        ctx.say(str(exc), "error")
        return
    _refresh(ctx)
    ctx.say(tr(f"Скилл «{entry['id']}» возвращён: {restored}", f"Restored “{entry['id']}”: {restored}"), "success")

async def view_skill(ctx: Context, row: Dict[str, Any]) -> None:
    body = f"{row['id']} — {skillkit.describe(row, disabled=not row.get('enabled', True))}\n"
    if row.get("path"):
        body += str(row["path"]) + "\n"
    body += "\n" + str(row.get("prompt") or "")
    await ctx.ui.open_document(tr(f"Скилл {row['id']}", f"Skill {row['id']}"), body)

async def skill_menu(ctx: Context, row: Dict[str, Any]) -> None:
    """What to do with one skill."""
    slug = row["id"]
    while True:
        row = _row(ctx, slug) or row
        editable = bool(row.get("path")) and not row.get("builtin")
        options = [
            dlg.Option(tr("Использовать: /", "Use it: /") + slug, "use", tr("подставит команду в поле ввода — допишите задачу", "puts the command in the input box — add the task")),
            dlg.Option(tr("Посмотреть", "View"), "view"),
            dlg.Option(tr("Править…", "Edit…") if editable else tr("Сделать свою копию…", "Make my own copy…"), "edit",
                       tr("прямо здесь, в терминале", "right here in the terminal")),
            dlg.Option(tr("Включить", "Enable") if not row.get("enabled", True) else tr("Выключить", "Disable"), "toggle",
                       tr("выключенный не предлагается модели и не запускается", "a disabled skill is not offered to the model and does not run")),
        ]
        if editable:
            options.append(dlg.Option(tr("Удалить…", "Delete…"), "delete", tr("уйдёт в корзину", "goes to the trash")))
        options.append(dlg.Option(tr("Назад", "Back"), None))
        choice = await ctx.select(f"{slug} · {skillkit.describe(row, disabled=not row.get('enabled', True))}"[:110], options,
                                  body=ctx.r.render(Text("  " + (str(row.get("path")) if row.get("path") else tr("встроенный скилл", "built-in skill")), style="dim")))
        if choice is None:
            return
        if choice == "use":
            ctx.ui.input_buffer.text = f"/{slug} "
            ctx.ui.input_buffer.cursor_position = len(ctx.ui.input_buffer.text)
            return
        if choice == "view":
            await view_skill(ctx, row)
        elif choice == "edit":
            await edit_skill(ctx, row)
        elif choice == "toggle":
            await toggle_skill(ctx, row)
        elif choice == "delete":
            await delete_skill(ctx, row)
            if not _row(ctx, slug) or _row(ctx, slug).get("builtin"):
                return

async def manager(ctx: Context) -> None:
    while True:
        rows = ctx.runtime.skills.catalogue(refresh=True, include_disabled=True)
        options = [dlg.Option(tr("＋ Создать скилл…", "＋ Create a skill…"), "__new__",
                              tr("словами — модель напишет, или сами прямо здесь", "describe it and let MemuCode write it, or write it here"))]
        if skillkit.list_trash():
            options.append(dlg.Option(tr("↺ Вернуть удалённый…", "↺ Restore a deleted skill…"), "__restore__"))
        for row in rows:
            options.append(dlg.Option(row["id"], row["id"], skillkit.describe(row, disabled=not row.get("enabled", True)),
                                      hint="" if row.get("enabled", True) else "✗"))
        choice = await ctx.select(tr("Скиллы (Enter — действия, печатайте для поиска)", "Skills (Enter for actions, type to search)"),
                                  options, filterable=True, max_visible=9)
        if choice is None:
            return
        if choice == "__new__":
            await create_skill(ctx)
        elif choice == "__restore__":
            await restore_skill(ctx)
        else:
            row = _row(ctx, choice)
            if row:
                await skill_menu(ctx, row)

@command("skills", "Скиллы: список, создание, правка и удаление прямо в терминале (можно попросить модель написать)",
         "Skills: browse, create, edit and delete right in the terminal (the model can draft one for you)",
         hint="[new|edit|delete|disable|enable|view|restore] [name]", category="agents", while_busy=True)
async def skills(ctx: Context) -> None:
    words = ctx.words()
    action = words[0].lower() if words else ""
    name = skillkit.slugify(" ".join(words[1:])) if len(words) > 1 else ""
    if action in ("new", "create", "add", "создать"):
        await create_skill(ctx, " ".join(words[1:]))
        return
    if action in ("restore", "trash", "вернуть"):
        await restore_skill(ctx)
        return
    if action in ("reload", "refresh"):
        _refresh(ctx)
        ctx.say(tr("Скиллы перечитаны", "Skills reloaded"), "success")
        return
    if action in ("list", "ls"):
        for row in ctx.runtime.skills.catalogue(refresh=True, include_disabled=True):
            ctx.say(f"{row['id']}  ·  {skillkit.describe(row, disabled=not row.get('enabled', True))}"
                    + (f"  ·  {row['path']}" if row.get("path") else ""))
        return
    if action in ("edit", "delete", "remove", "rm", "disable", "enable", "view", "show", "править", "удалить"):
        if not name:
            ctx.say(tr(f"Укажите имя: /skills {action} <имя>", f"Name the skill: /skills {action} <name>"), "warn")
            return
        row = _row(ctx, name)
        if row is None:
            ctx.say(tr(f"Нет скилла «{name}». Список: /skills list", f"No skill named “{name}”. List them: /skills list"), "warn")
            return
        if action in ("edit", "править"):
            await edit_skill(ctx, row)
        elif action in ("delete", "remove", "rm", "удалить"):
            await delete_skill(ctx, row)
        elif action in ("disable", "enable"):
            await toggle_skill(ctx, row, enable=action == "enable")
        else:
            await view_skill(ctx, row)
        return
    await manager(ctx)
