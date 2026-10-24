"""Choosing models: ``/model auto``, ``/model notes`` and ``/agents``.

All three lean on the same idea: the user names *several* models for a job and says in a few words what each
one is good at. Whoever has to choose later — the decision model for ``/model auto``, the main model that
starts an agent, a keyword match when nothing else is connected — reads those remarks.
"""
from __future__ import annotations

from typing import Any, Dict, List

from rich.text import Text

from ..i18n import tr
from ..modelmeta import TIERS, model_notes, note_for, routing_config, set_model_note, worker_models
from ..tui import dialogs as dlg
from . import Context, command
from .model_cmds import pick_model, pick_models

def tier_text(tier: str) -> tuple:
    """(title, hint) of a routing tier, in the current language."""
    return {
        "fast": (tr("Простые запросы", "Simple requests"),
                 tr("вопросы, мелкие правки, поиск по коду — быстрая и дешёвая модель",
                    "questions, small edits, looking things up — a fast, cheap model")),
        "balanced": (tr("Обычная работа", "Everyday work"),
                     tr("средние задачи; необязательный уровень между быстрым и сильным",
                        "medium tasks; an optional step between fast and strong")),
        "strong": (tr("Сложная работа", "Hard work"),
                   tr("рефакторинг, отладка, архитектура, длинные задачи — самая сильная модель",
                      "refactoring, debugging, architecture, long tasks — your strongest model")),
    }[tier]


def _short(models: List[str], limit: int = 3) -> str:
    if not models:
        return tr("— не выбраны —", "— none —")
    shown = ", ".join(models[:limit])
    return shown + (f" +{len(models) - limit}" if len(models) > limit else "")


def _how_it_decides(ctx: Context) -> str:
    decisions = ctx.runtime.decisions
    uses = ctx.settings.get("decisions.uses") or {}
    if decisions.enabled and decisions.provider != "model" and uses.get("routing", True):
        return tr(f"решает модель решений {decisions.title} · {decisions.model}",
                  f"decided by the decision model {decisions.title} · {decisions.model}")
    return tr("решает эвристика и ваши заметки (подключите модель решений: /decisions)",
              "decided by a heuristic and your remarks (connect a decision model: /decisions)")


# ---------------------------------------------------------------- remarks

async def ask_remarks(ctx: Context, models: List[str], *, why: str = "") -> None:
    """One question per model: what is it good at? Esc skips a model; a kept remark is the default text."""
    notes = model_notes(ctx.settings)
    for model in models:
        body = ctx.r.render(Text("  " + (why or tr(
            "Короткая заметка помогает выбирать между моделями: «быстрая, хороша с CSS», «медленная, но точная в алгоритмах». "
            "Esc — пропустить.",
            "A short remark helps tell the models apart: “fast, good at CSS”, “slow but careful with algorithms”. "
            "Esc to skip.")), style="dim"))
        text = await ctx.ask(tr(f"Чем хороша модель {model}?", f"What is {model} good at?"), body=body,
                             default=notes.get(model, ""))
        if text is not None and text.strip():
            set_model_note(ctx.settings, model, text.strip())
            notes[model] = text.strip()


async def manage_notes(ctx: Context, args: str = "") -> None:
    """``/model notes`` — read, add, change and remove the remarks."""
    settings = ctx.settings
    words = args.split(None, 1)
    if words:
        models = ctx.runtime.router.cached_models() or await ctx.runtime.router.models()
        from ..tui.input import match_models
        found = match_models(words[0], models, limit=5)
        exact = [item for score, item in found if item.id.lower() == words[0].lower()] or (
            [found[0][1]] if len(found) == 1 else [])
        if not exact:
            ctx.say(tr(f"Не нашёл модель «{words[0]}» (или таких несколько): откройте /model notes",
                       f"No single model matches “{words[0]}”: open /model notes"), "warn")
            return
        model = exact[0].id
        if len(words) == 2:
            set_model_note(settings, model, words[1])
            ctx.say(tr(f"Заметка о {model}: {words[1]}", f"Remark about {model}: {words[1]}"), "success")
            return
        await ask_remarks(ctx, [model])
        return
    while True:
        notes = model_notes(settings)
        rows = [dlg.Option(model, model, text) for model, text in notes.items()]
        body = ctx.r.render(Text("  " + tr(
            "Заметки хранятся у MemuCode всегда: их видят модель решений (/model auto), главная модель при запуске агентов "
            "и сами пикеры моделей.",
            "Remarks are kept for good: the decision model (/model auto), the main model when it starts agents and the "
            "model pickers all see them."), style="dim"))
        options = [dlg.Option(tr("＋ Добавить или изменить заметку…", "＋ Add or change a remark…"), "__add__")]
        if notes:
            options.append(dlg.Option(tr("− Удалить заметку…", "− Remove a remark…"), "__remove__"))
        options += rows
        options.append(dlg.Option(tr("Готово", "Done"), None))
        choice = await ctx.select(tr("Заметки о моделях", "Remarks about models"), options, body=body,
                                  filterable=len(options) > 12)
        if choice is None:
            return
        if choice == "__add__":
            model = await pick_model(ctx, tr("О какой модели заметка?", "Which model is the remark about?"), current="")
            if model:
                await ask_remarks(ctx, [model])
        elif choice == "__remove__":
            gone = await ctx.select(tr("Какую заметку удалить?", "Remove which remark?"),
                                    [dlg.Option(model, model, text) for model, text in notes.items()])
            if gone:
                set_model_note(settings, gone, "")
                ctx.say(tr(f"Заметка о {gone} удалена", f"Remark about {gone} removed"), "success")
        elif choice in notes:
            await ask_remarks(ctx, [choice])


# ---------------------------------------------------------------- /model auto

def _routing_body(ctx: Context, config: Dict[str, Any]) -> str:
    notes = model_notes(ctx.settings)
    lines: List[Any] = [Text("  " + (tr("Автовыбор: ВКЛ", "Auto selection: ON") if config["enabled"] else
                                     tr("Автовыбор: выкл", "Auto selection: off")), style="bold"),
                        Text("  " + _how_it_decides(ctx), style="dim")]
    for tier in TIERS:
        label, _hint = tier_text(tier)
        models = config["tiers"][tier]
        lines.append(Text(f"  {label}: {_short(models)}"))
        for model in models[:4]:
            if notes.get(model):
                lines.append(Text(f"      {model} — “{notes[model]}”", style="dim"))
    return ctx.r.render(*lines)


async def manage_auto(ctx: Context, args: str = "") -> None:
    """``/model auto`` — one or several models per kind of request, each with a remark."""
    word = args.strip().lower()
    settings = ctx.settings
    if word in ("off", "выкл"):
        settings.set("model_routing.enabled", False)
        ctx.say(tr("Автовыбор модели выключен", "Model routing is off"), "success")
        return
    if word in ("on", "вкл"):
        config = routing_config(settings)
        if not any(config["tiers"].values()):
            ctx.say(tr("Сначала выберите модели: /model auto", "Choose models first: /model auto"), "warn")
            return
        settings.set("model_routing.enabled", True)
        ctx.say(tr("Автовыбор модели включён", "Model routing is on"), "success")
        return
    while True:
        config = routing_config(settings)
        options = []
        for tier in TIERS:
            label, hint = tier_text(tier)
            options.append(dlg.Option(f"{label}  →  {_short(config['tiers'][tier])}", tier, hint))
        options += [
            dlg.Option(tr("Заметки о моделях — чем каждая хороша…", "Remarks about the models — what each is good at…"), "notes",
                        tr("модель решений и главная модель читают их, выбирая модель", "the chooser reads them when picking a model")),
            dlg.Option(tr("Выключить автовыбор", "Turn auto selection off") if config["enabled"] else
                       tr("Включить автовыбор", "Turn auto selection on"), "toggle"),
            dlg.Option(tr("Готово", "Done"), None)]
        choice = await ctx.select(tr("Автовыбор модели", "Auto model selection"), options, body=_routing_body(ctx, config))
        if choice is None:
            break
        if choice == "notes":
            await manage_notes(ctx)
        elif choice == "toggle":
            if not config["enabled"] and not any(config["tiers"].values()):
                ctx.say(tr("Сначала выберите модели хотя бы для одного уровня.", "Choose models for at least one level first."), "warn")
                continue
            settings.set("model_routing.enabled", not config["enabled"])
        else:
            await _edit_tier(ctx, choice)
    config = routing_config(settings)
    if config["enabled"]:
        parts = [f"{tier_text(tier)[0]} → {_short(config['tiers'][tier], 2)}" for tier in TIERS if config["tiers"][tier]]
        ctx.say(tr("Автовыбор включён: " + "; ".join(parts) + f". {_how_it_decides(ctx).capitalize()}.",
                   "Auto selection is on: " + "; ".join(parts) + f". {_how_it_decides(ctx).capitalize()}."), "success")


async def _edit_tier(ctx: Context, tier: str) -> None:
    settings = ctx.settings
    config = routing_config(settings)
    label, hint = tier_text(tier)
    body = ctx.r.render(Text("  " + hint + tr(". Tab — отметить модель, Enter — готово. Можно несколько: тогда "
                                            "каждый раз выбирается лучшая по заметкам.",
                                            ". Tab ticks a model, Enter confirms. Pick several and the best one is "
                                            "chosen each time from your remarks."), style="dim"))
    picked = await pick_models(ctx, f"{label} — " + tr("какие модели", "which models"), selected=config["tiers"][tier],
                               body=body)
    if picked is None:
        return
    tiers = dict(config["tiers"])
    tiers[tier] = picked
    enabled = config["enabled"] or any(tiers.values())
    settings.set("model_routing", {"enabled": enabled, "tiers": tiers})
    ctx.runtime.invalidate_prompt()
    if len(picked) >= 2:
        missing = [model for model in picked if not note_for(settings, model)]
        if missing:
            await ask_remarks(ctx, missing, why=tr(
                f"Для уровня «{label}» выбрано несколько моделей. Напишите, чем каждая хороша — по этим заметкам будет "
                "выбираться модель для запроса. Esc — пропустить.",
                f"Several models serve “{label}”. Say what each is good at — the choice for a request is made from these "
                "remarks. Esc to skip."))


# ---------------------------------------------------------------- /agents

def pick_modes() -> dict:
    return {
        "model": (tr("Решает главная модель", "The main model decides"),
                  tr("при запуске агента она сама выбирает модель из списка (читает ваши заметки)",
                     "when it starts an agent it picks a model from the list (reading your remarks)")),
        "auto": (tr("Автоматически: модель решений и заметки", "Automatically: decision model and remarks"),
                 tr("выбор по тексту задачи агента, без участия главной модели",
                    "chosen from the agent's task, without the main model")),
        "first": (tr("Всегда первая из списка", "Always the first in the list"),
                  tr("остальные — запасные", "the others are spares")),
    }


@command("agents", "Модели для параллельных агентов: несколько моделей, ИИ выбирает подходящую для каждого агента",
         "Models for parallel agents: pick several, the AI chooses the right one for each agent", category="agents",
         while_busy=True)
async def agents(ctx: Context) -> None:
    settings = ctx.settings
    while True:
        models = worker_models(settings)
        mode = str(settings.get("worker_pick") or "model")
        notes = model_notes(settings)
        lines: List[Any] = [Text("  " + (tr("Агенты работают на: ", "Agents run on: ") + _short(models, 4) if models else
                                          tr("Агенты работают на главной модели.", "Agents run on the main model.")), style="bold")]
        for model in models[:6]:
            if notes.get(model):
                lines.append(Text(f"      {model} — “{notes[model]}”", style="dim"))
        if len(models) > 1:
            lines.append(Text("  " + pick_modes().get(mode, pick_modes()["model"])[0], style="dim"))
        lines.append(Text("  " + tr("/subtask <задача> запускает агента, /tasks показывает ход.",
                                    "/subtask <task> starts an agent, /tasks shows progress."), style="subtle"))
        options = [dlg.Option(tr("Выбрать модели для агентов…", "Choose the models for agents…"), "models",
                              tr("можно несколько — тогда для каждого агента выбирается подходящая",
                                 "several are fine — the right one is chosen for each agent"))]
        if len(models) > 1:
            options.append(dlg.Option(tr("Как выбирается модель для агента…", "How an agent's model is chosen…"), "pick",
                                      pick_modes().get(mode, pick_modes()["model"])[0]))
        if models:
            options.append(dlg.Option(tr("Заметки об этих моделях…", "Remarks about these models…"), "notes"))
            options.append(dlg.Option(tr("Использовать главную модель для всех агентов", "Use the main model for every agent"), "clear"))
        options.append(dlg.Option(tr("Готово", "Done"), None))
        choice = await ctx.select(tr("Агенты", "Agents"), options, body=ctx.r.render(*lines))
        if choice is None:
            break
        if choice == "models":
            picked = await pick_models(ctx, tr("Модели для агентов", "Models for agents"), selected=models, body=ctx.r.render(
                Text("  " + tr("Tab — отметить, Enter — готово.", "Tab ticks a model, Enter confirms."), style="dim")))
            if picked is None:
                continue
            settings.set("worker_models", picked)
            settings.set("worker_model", picked[0] if len(picked) == 1 else "")
            ctx.runtime.invalidate_prompt()
            if len(picked) >= 2:
                missing = [model for model in picked if not note_for(settings, model)]
                if missing:
                    await ask_remarks(ctx, missing, why=tr(
                        "Для агентов выбрано несколько моделей. Напишите, чем каждая хороша — по заметкам будет выбираться "
                        "модель для каждого агента. Esc — пропустить.",
                        "Several models serve agents. Say what each is good at — each agent's model is chosen from these "
                        "remarks. Esc to skip."))
        elif choice == "pick":
            picked_mode = await ctx.select(tr("Как выбирать модель для агента", "How to choose an agent's model"), [
                dlg.Option(label, key, hint, hint="✓" if key == mode else "") for key, (label, hint) in pick_modes().items()])
            if picked_mode:
                settings.set("worker_pick", picked_mode)
                ctx.runtime.invalidate_prompt()
        elif choice == "notes":
            await ask_remarks(ctx, models)
        elif choice == "clear":
            settings.set("worker_models", [])
            settings.set("worker_model", "")
            ctx.runtime.invalidate_prompt()
    models = worker_models(settings)
    ctx.say(tr("Агенты: " + (_short(models, 4) if models else "главная модель"),
               "Agents: " + (_short(models, 4) if models else "the main model")), "success")
