"""``/decisions`` — the decision model: which one judges, for what, and what happens when it fails.

A decision model writes no text. It reads a state and answers typed questions — yes/no odds, pick one option,
a score on a scale — in tenths of a second for a fraction of a cent. MemuCode asks it six kinds of question:

* **Auto mode** — how risky is this command or edit (instead of asking you every time);
* **/model auto** — how hard is this request, so which model should take it;
* **/agents** — which model should run this agent's task;
* **Skill hint** — does one of the skills fit the request;
* **/goal** — has the goal been reached;
* **Offer a plan** — is this a big task that should start with a plan.

OpenRouter lists every decision model it serves (Jev, Kev, Span, Solar, Mercury, Tev… and whatever a vendor adds
next); the picker here reads that list live, so a new model appears without an update. Each kind of check may
have a model of its own, and models to fall back to when the chosen one fails.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Set, Tuple

from rich.text import Text

from ... import decisions as dec
from .. import paths
from ..i18n import tr
from ..modelmeta import model_notes
from ..tui import dialogs as dlg
from ..tui.input import fuzzy_score
from . import Context, command
from .routing_cmds import ask_remarks

USES = ("auto_mode", "routing", "agents", "skills", "goal", "intent")

NEEDS: Dict[str, Set[str]] = {"auto_mode": {"score", "noul"}, "routing": {"score", "choice"}, "agents": {"choice"},
                              "skills": {"choice"}, "goal": {"noul"}, "intent": {"choice", "noul"}}

def use_text(use: str) -> Tuple[str, str]:
    """(name, what it does) of a kind of check, in the current language."""
    return {
        "auto_mode": (tr("Авто-режим", "Auto mode"),
                      tr("оценивает риск команд и правок вместо вопросов вам",
                         "judges how risky a command or an edit is, instead of asking you")),
        "routing": ("/model auto", tr("насколько сложен запрос → быстрая или сильная модель",
                                      "how hard the request is → a fast or a strong model")),
        "agents": ("/agents", tr("какая из выбранных моделей возьмёт задачу агента",
                                 "which of the chosen models takes an agent's task")),
        "skills": (tr("Подсказка скилла", "Skill hint"),
                   tr("подходит ли один из скиллов к запросу — подсказка модели и вам",
                      "does one of the skills fit the request — a hint for the model and for you")),
        "goal": ("/goal", tr("достигнута ли цель", "has the goal been reached")),
        "intent": (tr("Предложить план", "Offer a plan"),
                   tr("большая ли это задача — предложить начать с плана",
                      "is it a big task — offer to start with a plan")),
    }[use]

def catalog_cache():
    return paths.home() / "decisions-catalog.json"

def names() -> Dict[str, str]:
    """``{model id: readable name}`` from what is known about the decision models (the cached catalogue too)."""
    rows, _stamp = dec.read_catalog_cache(catalog_cache())
    found = {str(row["id"]): str(row.get("name") or "") for row in rows}
    for model_id, known in dec.KNOWN_MODELS.items():
        found.setdefault(model_id, known["name"])
    return found

def name_of(model: str, table: Optional[Dict[str, str]] = None) -> str:
    table = names() if table is None else table
    return table.get(model) or model or "—"

def _context_label(value: int) -> str:
    if not value:
        return ""
    return f"{value / 1_000_000:g}M" if value >= 1_000_000 else f"{value // 1000}k"

def _user_config(ctx: Context) -> Dict[str, Any]:
    """The ``decisions`` block of the user's own settings (not the merged one: defaults stay defaults)."""
    block = ctx.settings.layer("user").get("decisions")
    return dict(block) if isinstance(block, dict) else {}

def _save(ctx: Context, **changes: Any) -> None:
    config = _user_config(ctx)
    config.update(changes)
    ctx.settings.set("decisions", config)
    ctx.runtime.reload_decisions()

def _suited(row: Dict[str, Any], needs: Optional[Set[str]]) -> bool:
    kinds = row.get("kinds") or []
    return not needs or not kinds or needs <= set(kinds)

def _catalog_option(row: Dict[str, Any], *, current: str, remarks: Dict[str, str], needs: Optional[Set[str]]) -> dlg.Option:
    bits = [str(row["id"]), str(row.get("maker") or ""), dec.describe_price(row)]
    if row.get("context"):
        bits.append(tr("контекст ", "context ") + _context_label(int(row["context"])))
    if row.get("kinds"):
        bits.append(tr("отвечает: ", "answers: ") + ", ".join(dec.KIND_LABELS.get(kind, kind) for kind in row["kinds"]))
    text = " · ".join(bit for bit in bits if bit)
    remark = remarks.get(str(row["id"]))
    line = f"“{remark}”" if remark else str(row.get("note") or "")
    if line:
        text += "\n" + line
    suited = _suited(row, needs)
    if row["id"] == current:
        hint = "✓"
    elif not suited:
        hint = "⚠ " + tr("не для этой проверки", "not for this check")
    elif row.get("recommended"):
        hint = "★"
    elif row.get("free"):
        hint = "free"
    else:
        hint = ""
    return dlg.Option(str(row.get("name") or row["id"]), str(row["id"]), text, hint=hint)

def _catalog_matcher(by_id: Dict[str, Dict[str, Any]]):
    """Search by name, id, maker and the words "free"/"recommended": ``Kev``, ``respan``, ``free``, ``mercury``."""
    def score(query: str, option: dlg.Option) -> Optional[float]:
        row = by_id.get(str(option.value))
        if row is None:
            return None
        texts = [str(row.get("name") or ""), str(row["id"]), str(row.get("maker") or ""),
                 f"{row.get('maker') or ''} {row.get('name') or ''}"]
        if row.get("free"):
            texts.append("free бесплатно")
        if row.get("recommended"):
            texts.append("recommended рекомендуем")
        found = [value for value in (fuzzy_score(query, text) for text in texts if text) if value is not None]
        return max(found) if found else None
    return score

async def pick_from_catalog(ctx: Context, title: str, *, current: str = "", multi: bool = False,
                            selected: Optional[List[str]] = None, needs: Optional[Set[str]] = None,
                            none_label: str = "", query: str = "", body: Any = "",
                            exclude: Tuple[str, ...] = ()) -> Any:
    """Choose among the OpenRouter decision models. A single pick returns the id (``""`` for ``none_label``),
    a multi pick the list of ids; None when cancelled.

    The list opens at once from the saved or built-in catalogue and, when the saved one is stale, is brought
    up to date in place from OpenRouter while the picker is open (a multi pick waits for it instead — its ticks
    would be lost on a refresh)."""
    cache = catalog_cache()
    cached, _stamp = dec.read_catalog_cache(cache)
    rows = cached or dec.builtin_catalog()
    fetch = asyncio.ensure_future(dec.load_catalog(cache, transport=ctx.runtime.decision_transport))
    try:
        rows, _how = await asyncio.wait_for(asyncio.shield(fetch), 0.3)
    except asyncio.TimeoutError:
        if multi:
            ctx.ui.status_text = tr("загружаю список моделей решений", "loading the list of decision models")
            try:
                rows, _how = await fetch
            except Exception:
                pass
            finally:
                ctx.ui.status_text = ""
    except Exception:
        pass
    remarks = model_notes(ctx.settings)
    by_id: Dict[str, Dict[str, Any]] = {}

    def build(found: List[Dict[str, Any]]) -> List[dlg.Option]:
        by_id.update({str(row["id"]): row for row in found})
        ordered = sorted((row for row in found if str(row["id"]) not in exclude),
                         key=lambda row: (row["id"] != current, not _suited(row, needs), not row.get("recommended"),
                                          not row.get("free"), float(row.get("price_input") or 0.0),
                                          str(row.get("name") or "").lower()))
        options: List[dlg.Option] = []
        if none_label:
            options.append(dlg.Option(none_label, "", tr("без отдельной модели", "no model of its own"),
                                      hint="✓" if not current else ""))
        options += [_catalog_option(row, current=current, remarks=remarks, needs=needs) for row in ordered]
        return options

    shown = {str(row["id"]) for row in rows}
    options = build(rows)
    initial = ""
    if query:
        exact = [row for row in rows if str(row["id"]).lower() == query.lower() or str(row.get("name") or "").lower() == query.lower()]
        if len(exact) == 1 and not multi:
            return exact[0]["id"]
        matcher = _catalog_matcher(by_id)
        hits = [row for row in rows if matcher(query, dlg.Option(row["id"], row["id"])) is not None]
        if len(hits) == 1 and not multi:
            return hits[0]["id"]
        initial = query

    async def refresh(dialog: dlg.Dialog) -> None:
        fresh, _how = await fetch
        if fresh and {str(row["id"]) for row in fresh} != shown:
            dialog.set_options(build(fresh))

    try:
        choice = await ctx.select(title, options, body=body, filterable=True, multi=multi, max_visible=8,
                                  selected=selected if multi else None, initial_filter=initial, allow_empty=multi,
                                  matcher=_catalog_matcher(by_id),
                                  updater=None if multi or fetch.done() else refresh)
    finally:
        if not fetch.done():
            fetch.cancel()
    if choice and not multi and needs and choice in by_id and not _suited(by_id[choice], needs):
        row = by_id[choice]
        kinds = ", ".join(dec.KIND_LABELS.get(kind, kind) for kind in row.get("kinds") or [])
        if not await ctx.confirm(
                tr(f"{row.get('name') or choice} умеет только: {kinds}", f"{row.get('name') or choice} only answers: {kinds}"),
                tr("Этой проверке нужны и другие типы вопросов. Модель будет пропускаться, а решать будет следующая "
                   "(или встроенные правила). Всё равно выбрать?",
                   "This check needs other kinds of question too. The model will be skipped and the next one (or the "
                   "built-in rules) will decide. Choose it anyway?"), danger=True):
            return None
    return choice

async def pick_model(ctx: Context, title: str, *, current: str = "", needs: Optional[Set[str]] = None,
                     none_label: str = "", query: str = "", body: Any = "") -> Optional[str]:
    """One decision model of the connected provider: from the live catalogue (OpenRouter), from the provider's
    own list (TypeSafe) or typed in (a compatible endpoint)."""
    client = ctx.runtime.decisions
    if client.provider == "openrouter":
        return await pick_from_catalog(ctx, title, current=current, needs=needs, none_label=none_label, query=query,
                                       body=body)
    rows: List[str] = []
    if client.enabled and client.provider in ("typesafe", "custom"):
        ctx.ui.status_text = tr("запрашиваю список моделей", "asking for the list of models")
        try:
            rows = [str(row.get("id")) for row in await asyncio.wait_for(client.list_models(), 8) if row.get("id")]
        except Exception:
            rows = []
        finally:
            ctx.ui.status_text = ""
    if rows:
        options = ([dlg.Option(none_label, "", hint="✓" if not current else "")] if none_label else []) + [
            dlg.Option(model, model, hint="✓" if model == current else "") for model in rows]
        return await ctx.select(title, options, body=body, filterable=len(options) > 12,
                                initial_filter=query if len(rows) > 1 else "")
    text = await ctx.ask(title, body=body or ctx.r.render(Text("  " + tr(
        "Название модели (список получить не удалось). Пусто — как у остальных проверок.",
        "Model name (the list could not be read). Empty — like the other checks."), style="dim")), default=current)
    return None if text is None else text.strip()

def _provider_rows() -> List[dlg.Option]:
    return [
        dlg.Option(tr("OpenRouter — все модели решений (рекомендуется)", "OpenRouter — every decision model (recommended)"),
                   "openrouter", tr("один ключ: Jev, Kev, Span, Solar, Mercury, Tev… есть бесплатные; новые появляются сами",
                                    "one key: Jev, Kev, Span, Solar, Mercury, Tev… some are free; new ones appear on their own")),
        dlg.Option(tr("TypeSafe напрямую — Jev", "TypeSafe directly — Jev"), "typesafe",
                   tr("ключ на typesafe.ai · модель jev-latest", "key from typesafe.ai · model jev-latest")),
        dlg.Option(tr("Свой совместимый адрес", "Your own compatible endpoint"), "custom",
                   tr("сервер в формате Decisions (System One)", "a server in the Decisions (System One) format")),
        dlg.Option(tr("Без ключа: обычная чат-модель отвечает в том же формате", "No key: a chat model answers in the same format"),
                   "model", tr("медленнее и дороже, но работает везде; модель — /small-model",
                               "slower and pricier, but works anywhere; the model is /small-model")),
    ]

def _status_body(ctx: Context) -> Any:
    runtime = ctx.runtime
    client = runtime.decisions
    config = ctx.settings.get("decisions") or {}
    table = names()
    lines: List[Any] = []
    if client.provider in ("off", ""):
        lines.append(Text("  " + tr("Сейчас: выключена. Встроенные правила и быстрая чат-модель решают сами.",
                                    "Now: off. Built-in rules and a fast chat model decide on their own."), style="bold"))
    elif not client.enabled:
        lines.append(Text("  " + tr("Сейчас: ", "Now: ") + client.title + " — "
                                    + tr("не хватает ключа или адреса", "a key or an address is missing"), style="bold"))
    else:
        shown = tr("чат-модель", "a chat model") if client.provider == "model" else name_of(client.model, table)
        lines.append(Text(f"  {tr('Сейчас', 'Now')}: {client.title} · {shown}", style="bold"))
        source = {"own": tr("ключ введён здесь", "key entered here"),
                  "connection": tr("ключ из подключения OpenRouter (/proxy)", "key from your OpenRouter connection (/proxy)"),
                  "environment": tr("ключ из переменной окружения", "key from an environment variable")}.get(
            runtime.decision_key_source(), "")
        if source and client.provider != "model":
            lines.append(Text("  " + source, style="dim"))
        own = {use: model for use, model in (config.get("use_models") or {}).items() if model and use in USES}
        for use in USES:
            if use in own:
                lines.append(Text(f"  {use_text(use)[0]} → {name_of(own[use], table)}", style="dim"))
        if client.fallbacks:
            lines.append(Text("  " + tr("Запасные: ", "Backups: ") + ", ".join(name_of(item, table) for item in client.fallbacks[:4]),
                              style="dim"))
        uses = config.get("uses") or {}
        off = [use_text(use)[0] for use in USES if not uses.get(use, True)]
        if off:
            lines.append(Text("  " + tr("Выключено: ", "Switched off: ") + ", ".join(off), style="dim"))
        stats = runtime.stats
        if stats.decision_calls:
            from ... import pricing
            lines.append(Text("  " + tr(f"В этой сессии: {stats.decision_calls} запросов · "
                                        f"{pricing.format_usd(stats.decision_cost, precise=True)}",
                                        f"This session: {stats.decision_calls} calls · "
                                        f"{pricing.format_usd(stats.decision_cost, precise=True)}"), style="subtle"))
    return ctx.r.render(ctx.r.md(tr(
        "**Модель решений** не пишет текст: за доли секунды она отвечает числами — «насколько рискованна команда», «какой "
        "скилл подойдёт», «сложный ли запрос». Так MemuCode реже вас дёргает и точнее выбирает модель. Стоит копейки за "
        "миллион токенов.",
        "A **decision model** writes no text: in a fraction of a second it answers with numbers — how risky a command is, "
        "which skill fits, how hard a request is. MemuCode interrupts you less and picks models better. It costs cents "
        "per million tokens.")), *lines)

def _key_on_hand(ctx: Context, provider: str, config: Dict[str, Any], same: bool) -> str:
    """Where a key for ``provider`` already is, in words for the menu — or "" when there is none."""
    import os
    if same and str(config.get("api_key") or "").strip():
        return tr("ключ, сохранённый здесь", "the key saved here")
    if provider == "openrouter" and any(
            "openrouter.ai" in str(row.get("base_url") or "") and row.get("api_key") and not row.get("from_env")
            for row in ctx.settings.connections()):
        return tr("ключ вашего подключения OpenRouter (/proxy)", "the key of your OpenRouter connection (/proxy)")
    env = str(dec.PROVIDERS[provider].get("key_env") or "")
    if env and os.environ.get(env, "").strip():
        return tr(f"переменная окружения {env}", f"the {env} environment variable")
    return ""

async def connect(ctx: Context) -> bool:
    """Choose where the decisions come from and give the key. False when cancelled."""
    config = ctx.settings.get("decisions") or {}
    current = str(config.get("provider") or "off")
    previous = current if current != "off" else str(config.get("last_provider") or "off")
    rows = _provider_rows()
    choice = await ctx.select(tr("Откуда брать решения", "Where the decisions come from"), rows,
                              index=max(0, next((i for i, row in enumerate(rows) if row.value == previous), 0)))
    if not choice:
        return False
    spec = dec.PROVIDERS[choice]
    same = choice == previous
    changes: Dict[str, Any] = {"provider": choice, "last_provider": ""}
    if not same:

        changes.update({"model": "", "use_models": {}, "fallbacks": [], "api_key": "", "base_url": "", "path": ""})
    if choice == "custom":
        base = await ctx.ask(tr("Адрес (base URL)", "Base URL"), default=str(config.get("base_url") or "") if same else "")
        if not base:
            return False
        changes["base_url"] = base.strip()
        path = await ctx.ask(tr("Путь запроса", "Request path"), default=str(config.get("path") or "/v1/systemone") if same else "/v1/systemone")
        changes["path"] = (path or "/v1/systemone").strip()
    if choice in ("typesafe", "openrouter", "custom"):
        hint = tr(f"Ключ: {spec['key_url']}", f"Get a key: {spec['key_url']}") if spec.get("key_url") else ""
        have = _key_on_hand(ctx, choice, config, same)
        enter = True
        if have:
            use = await ctx.select(tr("API-ключ", "API key"), [
                dlg.Option(tr("Использовать: ", "Use: ") + have, "have"),
                dlg.Option(tr("Ввести другой ключ…", "Enter another key…"), "new")],
                body=ctx.r.render(Text("  " + hint, style="dim")) if hint else "")
            if use is None:
                return False
            enter = use == "new"
        if enter:
            key = await ctx.ask(tr("API-ключ", "API key"), body=ctx.r.render(Text("  " + hint, style="dim")) if hint else "")
            if not key:
                return False
            changes["api_key"] = key.strip()
    _save(ctx, **changes)
    if not ctx.runtime.decisions.enabled:
        ctx.say(tr("Не хватает ключа или адреса — подключение пока не активно.", "A key or an address is missing — not active yet."),
                "warn")
    return True

async def choose_default(ctx: Context, query: str = "") -> bool:
    client = ctx.runtime.decisions
    if client.provider == "model":
        ctx.say(tr("Здесь отвечает обычная чат-модель: выберите её командой /small-model.",
                   "A chat model answers here: choose it with /small-model."))
        return False
    if client.provider in ("off", ""):
        ctx.say(tr("Сначала подключите модель решений.", "Connect a decision model first."), "warn")
        return False
    picked = await pick_model(ctx, tr("Какая модель решает", "Which model decides"), current=client.model, query=query,
                              body=ctx.r.render(Text("  " + tr(
                                  "Так будут решать все проверки, кроме тех, для которых выбрана своя модель. "
                                  "Печатайте, чтобы искать: имя, автор, free.",
                                  "Every check uses it except those given a model of their own. Type to search: a name, "
                                  "a maker, “free”."), style="dim")))
    if not picked:
        return False
    _save(ctx, model=picked)
    ctx.say(tr(f"Решает {name_of(picked)}", f"{name_of(picked)} decides"), "success")
    return True

async def per_use(ctx: Context) -> None:
    """A model of its own for each kind of check."""
    while True:
        client = ctx.runtime.decisions
        own = {use: model for use, model in (ctx.settings.get("decisions.use_models") or {}).items() if model}
        table = names()
        options = []
        for use in USES:
            label, hint = use_text(use)
            options.append(dlg.Option(label, use, hint,
                                      hint=name_of(own[use], table) if use in own else tr("как у остальных", "like the rest")))
        if own:
            options.append(dlg.Option(tr("Сбросить: одна модель для всего", "Reset: one model for everything"), "__reset__"))
        options.append(dlg.Option(tr("Готово", "Done"), None))
        body = ctx.r.render(Text("  " + tr(
            "Лёгкие проверки (подсказка скилла) могут идти на бесплатную модель, а оценка риска в авто-режиме — на самую "
            "надёжную. По умолчанию решает: ", "Light checks (the skill hint) can use a free model while the risk check of "
            "auto mode gets the most careful one. By default: ") + name_of(client.model, table), style="dim"))
        choice = await ctx.select(tr("Своя модель для каждой проверки", "A model for each check"), options, body=body)
        if choice is None:
            return
        if choice == "__reset__":
            _save(ctx, use_models={})
            continue
        label, _hint = use_text(choice)
        picked = await pick_model(ctx, tr(f"Модель для: {label}", f"Model for: {label}"), current=own.get(choice, ""),
                                  needs=NEEDS[choice], none_label=tr("Как у остальных проверок", "Like the other checks"))
        if picked is None:
            continue
        updated = dict(own)
        if picked and picked != client.model:
            updated[choice] = picked
        else:
            updated.pop(choice, None)
        _save(ctx, use_models=updated)

async def backups(ctx: Context) -> None:
    """Models to try, in turn, when the chosen one fails (a network error, an overload, a missing model)."""
    client = ctx.runtime.decisions
    chosen = list(client.fallbacks)
    body = ctx.r.render(Text("  " + tr(
        "Если выбранная модель не отвечает, спросим следующую из этого списка — вместо того чтобы вернуться к встроенным "
        "правилам. Tab — отметить, Enter — готово. Порядок: как в списке.",
        "When the chosen model does not answer, the next one in this list is asked — instead of dropping to the "
        "built-in rules. Tab ticks, Enter confirms. The order is the order of the list."), style="dim"))
    if client.provider == "openrouter":
        picked = await pick_from_catalog(ctx, tr("Запасные модели решений", "Backup decision models"), multi=True,
                                         selected=chosen, body=body, exclude=(client.model,))
    else:
        text = await ctx.ask(tr("Запасные модели (через запятую)", "Backup models (comma separated)"), body=body,
                             default=", ".join(chosen))
        picked = None if text is None else [item.strip() for item in text.split(",") if item.strip()]
    if picked is None:
        return
    _save(ctx, fallbacks=list(dict.fromkeys(picked)))
    ctx.say(tr("Запасные: " + (", ".join(name_of(item) for item in picked) or "нет"),
               "Backups: " + (", ".join(name_of(item) for item in picked) or "none")), "success")

async def uses_menu(ctx: Context) -> None:
    """What the decision model is used for (space toggles)."""
    uses = ctx.settings.get("decisions.uses") or {}
    options = [dlg.Option(use_text(use)[0], use, use_text(use)[1]) for use in USES]
    dialog = dlg.Dialog(title=tr("Для чего использовать модель решений (пробел — отметить)", "What the decision model is used for (space to toggle)"),
                        options=options, multi=True, allow_empty=True)
    dialog.selected = {index for index, use in enumerate(USES) if uses.get(use, True)}
    chosen = await ctx.ui.open_dialog(dialog)
    if chosen is None:
        return
    on = set(chosen)
    _save(ctx, uses={use: use in on for use in USES})
    ctx.say(tr("Сохранено", "Saved"), "success")

async def remarks(ctx: Context) -> None:
    """What the user thinks of each decision model — kept for good and shown in every picker."""
    while True:
        notes = model_notes(ctx.settings)
        known = {row["id"] for row in dec.builtin_catalog()} | {row["id"] for row in dec.read_catalog_cache(catalog_cache())[0]}
        mine = {model: text for model, text in notes.items() if model in known}
        model = await pick_from_catalog(
            ctx, tr("Заметка о модели решений", "A remark about a decision model"),
            body=ctx.r.render(Text("  " + tr(
                "Заметки видны в списках моделей и MemuAI-ассистенту. У вас: ", "Remarks show up in the model lists and are known to "
                "the assistant. You have: ") + str(len(mine)), style="dim")))
        if not model:
            return
        await ask_remarks(ctx, [model], why=tr(
            "Например: «быстрая, для оценки риска», «бесплатная, годится для подсказок скиллов». Пусто — убрать заметку.",
            "For example: “fast, good for the risk check”, “free, fine for skill hints”. Empty removes the remark."))

async def test_connection(ctx: Context) -> None:
    """One tiny request to every model in use: the key, the endpoint and each model."""
    client = ctx.runtime.decisions
    if not client.enabled:
        ctx.say(tr("Модель решений не подключена или не хватает ключа: /decisions", "No decision model, or a key is missing: /decisions"), "warn")
        return
    models = list(dict.fromkeys([client.model, *[model for model in client.use_models.values() if model], *client.fallbacks]))
    if client.provider == "model" or not models or models == [""]:
        models = [client.model]
    table = names()
    ctx.ui.status_text = tr("проверяю модель решений", "testing the decision model")
    try:
        for model in models:
            label = tr("чат-модель", "chat model") if client.provider == "model" else name_of(model, table)
            probe = client.for_model(model)
            try:
                result = await asyncio.wait_for(probe.test(), 25)
            except Exception as exc:
                ctx.say(f"✗ {label} — {exc}", "error")
                continue
            extra = []
            if result.get("refund") is not None:
                extra.append(f"refund={result['refund']:.2f}")
            if result.get("tone"):
                extra.append(f"tone={result['tone']}")
            ctx.say(f"✓ {label} — {result['latency_ms']} ms" + (" · " + ", ".join(extra) if extra else "")
                    + (f" · ${result['cost']:.6f}" if result.get("cost") else ""), "success")
    finally:
        ctx.ui.status_text = ""

def _switch(ctx: Context, on: bool) -> bool:
    config = ctx.settings.get("decisions") or {}
    if on:
        last = str(config.get("last_provider") or "")
        if not last or last == "off":
            return False
        _save(ctx, provider=last, last_provider="")
        return True
    current = str(config.get("provider") or "off")
    if current != "off":
        _save(ctx, provider="off", last_provider=current)
    return True

@command("decisions", "Модель решений: быстрые проверки — безопасность, выбор модели и скиллов, план, цель",
         "Decision model: fast checks — safety, model and skill choice, plans, goals", aliases=("jev",),
         hint="[status|test|model|on|off]", category="config", while_busy=True)
async def decisions(ctx: Context) -> None:
    words = ctx.args.split(None, 1)
    sub = words[0].lower() if words else ""
    rest = words[1].strip() if len(words) > 1 else ""
    if sub in ("off", "выкл"):
        _switch(ctx, False)
        ctx.say(tr("Модель решений выключена (настройки сохранены: /decisions on)", "Decision model turned off (settings are kept: /decisions on)"), "success")
        return
    if sub in ("on", "вкл"):
        if _switch(ctx, True):
            ctx.say(tr("Модель решений включена", "Decision model turned on"), "success")
            return
        sub = ""
    if sub in ("test", "проверка"):
        await test_connection(ctx)
        return
    if sub == "status":
        ctx.print(_status_body(ctx))
        return
    if sub in ("model", "модель"):
        await choose_default(ctx, rest)
        return
    client = ctx.runtime.decisions
    while True:
        client = ctx.runtime.decisions
        config = ctx.settings.get("decisions") or {}
        connected = client.provider not in ("off", "")
        options: List[dlg.Option] = []
        if not connected:
            if config.get("last_provider"):
                options.append(dlg.Option(tr("Включить снова", "Turn it back on"), "on",
                                          tr("с прежними настройками", "with the settings as they were")))
            options.append(dlg.Option(tr("Подключить…", "Connect…"), "connect"))
        else:
            options.append(dlg.Option(tr("Какая модель решает…", "Which model decides…"), "model",
                                      name_of(client.model) if client.provider != "model" else tr("чат-модель", "a chat model")))
            if client.provider != "model":
                own = len([1 for model in client.use_models.values() if model])
                options.append(dlg.Option(tr("Своя модель для каждой проверки…", "A model for each check…"), "per_use",
                                          tr(f"сейчас отдельных: {own}", f"separate ones now: {own}") if own else
                                          tr("например, бесплатная для подсказок, надёжная для оценки риска",
                                             "for example a free one for hints, a careful one for the risk check")))
                options.append(dlg.Option(tr("Запасные модели…", "Backup models…"), "backups",
                                          ", ".join(name_of(item) for item in client.fallbacks[:3]) or
                                          tr("если выбранная не отвечает", "if the chosen one does not answer")))
            options.append(dlg.Option(tr("Для чего использовать…", "What to use it for…"), "uses"))
            options.append(dlg.Option(tr("Заметки о моделях решений…", "Remarks about decision models…"), "remarks"))
            options.append(dlg.Option(tr("Проверить подключение", "Test the connection"), "test"))
            options.append(dlg.Option(tr("Сменить подключение и ключ…", "Change the connection and key…"), "connect"))
            options.append(dlg.Option(tr("Выключить", "Turn off"), "off"))
        options.append(dlg.Option(tr("Готово", "Done"), None))
        choice = await ctx.select(tr("Модель решений", "Decision model"), options, body=_status_body(ctx))
        if choice is None:
            break
        if choice == "connect":
            if await connect(ctx) and ctx.runtime.decisions.enabled and not ctx.settings.get("decisions.model") \
                    and ctx.runtime.decisions.provider in ("openrouter", "typesafe"):
                await choose_default(ctx)
        elif choice == "on":
            _switch(ctx, True)
        elif choice == "model":
            await choose_default(ctx)
        elif choice == "per_use":
            await per_use(ctx)
        elif choice == "backups":
            await backups(ctx)
        elif choice == "uses":
            await uses_menu(ctx)
        elif choice == "remarks":
            await remarks(ctx)
        elif choice == "test":
            await test_connection(ctx)
        elif choice == "off":
            _switch(ctx, False)
