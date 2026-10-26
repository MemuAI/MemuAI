"""Model commands: /model /fast /effort /think /web /advisor /small-model /vision."""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional

from rich.panel import Panel
from rich.text import Text

from ... import pricing
from ..i18n import tr
from .. import vision as pictures
from ..llm import ModelInfo
from ..modelmeta import model_notes, note_for, routing_config
from ..tui import dialogs as dlg
from ..tui.input import match_models
from . import Context, command

def _context_label(value: int) -> str:
    if not value:
        return ""
    if value >= 1_000_000:
        return f"{value / 1_000_000:g}M"
    return f"{value // 1000}k"

def display_name(model: ModelInfo, aliases: Optional[Dict[str, str]] = None) -> str:
    """Round 33 (r33c-models-lists): the same readable name as in MemuAI — the user's own name, Proxy's catalogue
    name, else the shared rules (``app/model_names.py``: «aws9-claude-opus-4-6» → «Claude Opus 4.6»)."""
    alias = aliases.get(model.id) if isinstance(aliases, dict) else None
    if isinstance(alias, str) and alias.strip():
        return alias.strip()
    if model.name:
        return model.name
    try:
        from ... import model_names
        return model_names.pretty(model.id) or model.id
    except Exception:
        return model.id

def model_option(model: ModelInfo, current: str, sees: Optional[bool] = None,
                 aliases: Optional[Dict[str, str]] = None) -> dlg.Option:
    price = pricing.lookup(model.id, model.provider)
    name = display_name(model, aliases)
    bits = [model.provider or model.backend]
    if model.context:
        bits.append(tr("контекст ", "context ") + _context_label(model.context))
    if price is not None:
        bits.append(pricing.describe(price) + tr(" за 1M", " per 1M"))

    flags = [flag for flag, on in (("vision", model.vision if sees is None else sees), ("thinking", model.thinking),
                                   ("tools", model.native_tools)) if on]
    if flags:
        bits.append(", ".join(flags))
    return dlg.Option(name, model.id, " · ".join(bits), hint="✓" if model.id == current else "",
                      detail=model.id if name != model.id else "")

def _model_matcher(by_id: Dict[str, ModelInfo]):
    """Search of a model picker: provider, name, label and letters in order (see ``match_models``)."""
    def score(query: str, option: dlg.Option) -> Optional[float]:
        model = by_id.get(str(option.value))
        if model is None:
            return None
        found = match_models(query, [model], limit=1)
        if found:
            return found[0][0]

        words = [word for word in query.lower().split() if word]
        name = str(option.label or "").lower()
        return 150.0 if words and all(word in name for word in words) else None
    return score

async def _load_models(ctx: Context, *, chat_only: bool, only: Optional[Callable[[ModelInfo], bool]] = None) -> tuple:
    """(models, the fetch that refreshes them). The list opens from what is already known; the fresh one is
    fetched meanwhile and replaces it in place. Waiting for the Proxy first made every model command pause
    before anything appeared."""
    runtime = ctx.runtime
    refresh = asyncio.ensure_future(runtime.router.models(refresh=True))
    models = runtime.router.cached_models()
    if not models:
        ctx.ui.status_text = tr("загружаю модели", "loading models")
        try:
            models = await refresh
        finally:
            ctx.ui.status_text = ""
    models = [model for model in models if not (chat_only and (model.image or getattr(model, "kind", "chat") != "chat")) and (only is None or only(model))]
    if refresh.done():
        for name, error in runtime.router.errors.items():
            ctx.say(f"{name}: {error.splitlines()[0][:200]}", "warn")
    return models, refresh

def _model_rows(ctx: Context, current: str, allow_none: str = "", notes: bool = True):
    """A function ``rows(models) -> options``: the current model first, then pinned ones, then by provider."""
    runtime = ctx.runtime
    prefs = runtime.memuai_prefs()
    pinned = [str(item) for item in prefs.get("pinned_models") or []] if isinstance(prefs.get("pinned_models"), list) else []
    hidden = str(prefs.get("hidden_models") or "")
    remarks = model_notes(ctx.settings) if notes else {}
    aliases = prefs.get("model_aliases") if isinstance(prefs.get("model_aliases"), dict) else {}

    def rows(found: List[ModelInfo]) -> List[Any]:
        ordered = sorted(found, key=lambda model: (model.id != current, model.id not in pinned, model.provider, model.id))
        options: List[Any] = []
        if allow_none:
            options.append(dlg.Option(allow_none, "", ""))
        for model in ordered:
            if hidden and model.id in hidden.split(",") and model.id != current:
                continue
            option = model_option(model, current, pictures.support(runtime, model.id).can, aliases)
            if model.id in pinned:
                option.hint = (option.hint + " ★").strip() if option.hint else "★"
            if remarks.get(model.id):
                option.description = (option.description + "\n" if option.description else "") + "“" + remarks[model.id] + "”"
            options.append(option)
        return options
    return rows

async def pick_model(ctx: Context, title: str, *, current: str, query: str = "", allow_none: str = "",
                     chat_only: bool = True, only: Optional[Callable[[ModelInfo], bool]] = None) -> Optional[str]:
    models, refresh = await _load_models(ctx, chat_only=chat_only, only=only)
    if not models:
        ctx.say(tr("Нет доступных моделей. Запустите MemuAI Proxy или подключите API: /proxy",
                   "No models are available. Start MemuAI Proxy or connect an API with /proxy"), "warn")
        return None
    initial = ""
    if query:
        exact = [model for model in models if model.id.lower() == query.lower()]
        if exact:
            return exact[0].id
        found = match_models(query, models, limit=len(models))
        if len(found) == 1:
            return found[0][1].id
        if found:
            initial = query
        else:
            ctx.say(tr(f"Нет модели, подходящей к «{query}» — вот весь список",
                       f"No model matches “{query}” — here is the whole list"), "warn")
    rows = _model_rows(ctx, current, allow_none)
    by_id = {model.id: model for model in models}
    shown = set(by_id)

    async def update(dialog: dlg.Dialog) -> None:
        fresh = [model for model in await refresh
                 if not (chat_only and model.image) and (only is None or only(model))]
        if fresh and {model.id for model in fresh} != shown:
            by_id.update({model.id: model for model in fresh})
            dialog.set_options(rows(fresh))

    choice = await ctx.select(title, rows(models), filterable=True, index=0, max_visible=9,
                              updater=None if refresh.done() else update, initial_filter=initial,
                              matcher=_model_matcher(by_id))
    return choice

async def pick_models(ctx: Context, title: str, *, selected: List[str], body: str = "",
                      chat_only: bool = True) -> Optional[List[str]]:
    """Several models at once (space ticks a row, Enter confirms). None when cancelled."""
    models, refresh = await _load_models(ctx, chat_only=chat_only)
    if not models:
        ctx.say(tr("Нет доступных моделей. Запустите MemuAI Proxy или подключите API: /proxy",
                   "No models are available. Start MemuAI Proxy or connect an API with /proxy"), "warn")
        return None
    known = {model.id for model in models}
    rows = _model_rows(ctx, "")
    by_id = {model.id: model for model in models}
    for missing in selected:
        if missing not in known:
            by_id[missing] = ModelInfo(id=missing, provider=missing.split("-", 1)[0])
    options = rows(list(by_id.values()))
    answer = await ctx.select(title, options, multi=True, filterable=True, max_visible=9, body=body,
                              selected=selected, matcher=_model_matcher(by_id), allow_empty=True)
    if answer is None:
        return None
    return [str(item) for item in answer]

@command("model", "Выбрать модель (Alt+P). Ищет по имени и провайдеру. /model auto — автовыбор, /model notes — заметки",
         "Choose the model (Alt+P) — search by name or provider. /model auto routes by difficulty, /model notes",
         hint="[model|auto|notes]", category="model", while_busy=True, immediate=True)
async def model(ctx: Context) -> None:
    runtime = ctx.runtime
    query = ctx.args.strip()
    word = query.lower()
    if word in ("auto", "авто") or word.startswith(("auto ", "авто ")):
        from .routing_cmds import manage_auto
        await manage_auto(ctx, query.split(None, 1)[1] if " " in query else "")
        return
    if word in ("off", "manual") and routing_config(ctx.settings)["enabled"]:
        ctx.settings.set("model_routing.enabled", False)
        ctx.say(tr("Автовыбор модели выключен", "Model routing is off"), "success")
        return
    if word in ("notes", "note", "заметки") or word.startswith(("notes ", "note ")):
        from .routing_cmds import manage_notes
        await manage_notes(ctx, query.split(None, 1)[1] if " " in query else "")
        return
    chosen = await pick_model(ctx, tr("Модель для MemuCode", "Model for MemuCode"), current=runtime.model, query=query)
    if not chosen:
        return
    runtime.set_model(chosen)
    ctx.settings.set("model", chosen)
    window = runtime.effective_window(chosen)
    remark = note_for(ctx.settings, chosen)
    info = runtime.router.info(chosen) if hasattr(runtime.router, "info") else None
    price = pricing.lookup(chosen, getattr(info, "provider", "") or "")
    shown = display_name(info, runtime.memuai_prefs().get("model_aliases") or {}) if info is not None else chosen
    if shown != chosen:
        shown = f"{shown} ({chosen})"
    ctx.say(tr(f"Модель: {shown} · контекст {_context_label(window)}", f"Model: {shown} · context {_context_label(window)}")
            + (f" · {pricing.describe(price)}" if price else "") + (f" · “{remark}”" if remark else ""), "success")
    if routing_config(ctx.settings)["enabled"]:
        ctx.say(tr("Автовыбор включён и решает за каждый запрос; эта модель — основная. /model off — отключить автовыбор.",
                   "Auto selection is on and decides each request; this model is the main one. /model off turns it off."))

@command("fast", "Переключиться на быструю модель и обратно", "Toggle the fast model", category="model", while_busy=True)
async def fast(ctx: Context) -> None:
    runtime = ctx.runtime
    small = str(ctx.settings.get("small_model") or (routing_config(ctx.settings)["tiers"]["fast"] or [""])[0] or "")
    if not small:
        small = await pick_model(ctx, tr("Какую модель считать быстрой?", "Which model is the fast one?"), current="") or ""
        if not small:
            return
        ctx.settings.set("small_model", small)
    main = str(ctx.settings.get("model") or "")
    if runtime.model == small and main and main != small:
        runtime.set_model(main)
        ctx.say(tr(f"Обычная модель: {main}", f"Back to {main}"), "success")
    else:
        runtime.set_model(small)
        ctx.say(tr(f"Быстрый режим: {small} (/fast — вернуться)", f"Fast mode: {small} (/fast to switch back)"), "success")

@command("small-model", "Быстрая модель для заголовков, сжатия, /btw и проверок", "Fast model for titles, compaction, /btw and checks",
         hint="[model]", aliases=("fast-model",), category="model", while_busy=True)
async def small_model(ctx: Context) -> None:
    chosen = await pick_model(ctx, tr("Быстрая вспомогательная модель", "Fast helper model"),
                              current=str(ctx.settings.get("small_model") or ""), query=ctx.args.strip(),
                              allow_none=tr("Как основная модель", "Same as the main model"))
    if chosen is None:
        return
    ctx.settings.set("small_model", chosen)
    ctx.say(tr(f"Вспомогательная модель: {chosen or 'как основная'}", f"Helper model: {chosen or 'same as main'}"), "success")

def _vision_status(ctx: Context) -> List[str]:
    """What happens to a picture right now, in plain words."""
    runtime = ctx.runtime
    verdict = pictures.support(runtime, runtime.model)
    chosen = pictures.configured_model(runtime)
    describer = pictures.image_model(runtime, [runtime.model])
    reasons = {"learned": tr("проверено на деле", "confirmed by a real request"),
               "list": tr("так сказано в списке моделей", "the model list says so"),
               "name": tr("судя по названию", "judging by its name"),
               "api": tr("API Anthropic", "the Anthropic API"),
               "adapter": tr("провайдер не передаёт картинки", "the provider does not pass pictures"),
               "unknown": tr("неизвестно", "unknown")}
    why = reasons.get(verdict.source, "")
    if pictures.mode(runtime) == "off":
        first = tr("Описание изображений выключено: модели без зрения получат только пометку «было изображение». "
                   "Включить: /vision on",
                   "Image descriptions are off: a model without eyes only gets a note that there was an image. "
                   "Turn on: /vision on")
    elif verdict.can is True:
        first = tr(f"{runtime.model} видит изображения ({why}) — картинки идут к ней как есть.",
                   f"{runtime.model} sees images ({why}) — pictures go to it as they are.")
    elif verdict.can is False:
        first = tr(f"{runtime.model} не видит изображения ({why}) — картинку опишет {describer or '—'} "
                   "по задаче основной модели.",
                   f"{runtime.model} cannot see images ({why}) — {describer or '—'} describes the picture for the "
                   "main model's task.")
    else:
        first = tr(f"{runtime.model}: видит ли изображения, неизвестно — картинку отправлю как есть, а при отказе "
                   f"опишет {describer or '—'}.",
                   f"{runtime.model}: unknown whether it sees images — the picture is sent as it is, and if refused "
                   f"{describer or '—'} describes it.")
    second = (tr(f"Модель для изображений: {chosen} (общая с MemuAI).", f"Image model: {chosen} (shared with MemuAI).")
              if chosen else tr(f"Модель для изображений не выбрана: беру лучшую из подключённых — {describer or 'нет'}.",
                                f"No image model chosen: the best connected one is used — {describer or 'none'}."))
    lines = [first, second]
    if chosen and pictures.support(runtime, chosen).can is False:
        lines.append(tr(f"Внимание: {chosen} не видит изображения (проверено) — вместо неё используется {describer or '—'}. "
                        "Выберите другую командой /vision или перепроверьте: /vision test " + chosen,
                        f"Warning: {chosen} cannot see images (checked) — {describer or '—'} is used instead. "
                        "Pick another with /vision or check again: /vision test " + chosen))
    return lines

@command("vision", "Модель для изображений: описывает картинки для моделей без зрения (общая с MemuAI)",
         "Image model: describes pictures for models that cannot see (shared with MemuAI)",
         hint="[model | auto | on | off | test]", category="model", while_busy=True)
async def vision(ctx: Context) -> None:
    runtime = ctx.runtime
    query = ctx.args.strip()
    word = query.lower()
    if word in ("off", "выкл", "выключить"):
        ctx.settings.set("vision_mode", "off")
        runtime.invalidate_prompt()
        ctx.say(tr("Описание изображений выключено.", "Image descriptions are off."), "success")
        return
    if word in ("on", "вкл", "включить"):
        ctx.settings.set("vision_mode", "auto")
        runtime.invalidate_prompt()
        ctx.say(tr("Описание изображений включено.", "Image descriptions are on."), "success")
        return
    if word in ("auto", "авто"):
        pictures.save_choice(runtime, "")
        runtime.invalidate_prompt()
        ctx.say(tr("Модель для изображений: выбираю сама лучшую из подключённых.",
                   "Image model: the best connected one is chosen automatically."), "success")
        return
    if word.split(" ", 1)[0] in ("test", "check", "проверка", "тест"):
        await _vision_test(ctx, query.split(" ", 1)[1].strip() if " " in query else "")
        return
    if word in ("status", "статус"):
        for line in _vision_status(ctx):
            ctx.say(line, "info")
        return
    chosen = await pick_model(
        ctx, tr("Модель для изображений (описывает картинки; ещё для Browser и Computer Use)",
                "Image model (describes pictures; also used by Browser and Computer Use)"),
        current=pictures.configured_model(runtime), query=query,
        allow_none=tr("Выбирать автоматически (лучшую из подключённых)", "Choose automatically (the best connected one)"),
        only=lambda model: pictures.support(runtime, model.id).can is not False)
    if chosen is None:
        return
    shared = pictures.save_choice(runtime, chosen)
    runtime.invalidate_prompt()
    ctx.say(tr(f"Модель для изображений: {chosen or 'автоматически'}" + (" — так же в MemuAI." if shared else ""),
               f"Image model: {chosen or 'automatic'}" + (" — the same in MemuAI." if shared else "")), "success")
    for line in _vision_status(ctx)[:1] + _vision_status(ctx)[2:]:
        ctx.say(line, "info")
    if chosen:
        ctx.say(tr("Проверить, что она действительно читает картинки: /vision test",
                   "To check that it really reads pictures: /vision test"), "info")

async def _vision_test(ctx: Context, target: str) -> None:
    """A real request with a picture that has a random code on it: the model sees it or it does not."""
    runtime = ctx.runtime
    models = [target] if target else [runtime.model]
    chosen = pictures.image_model(runtime, [runtime.model])
    if not target and chosen and chosen not in models:
        models.append(chosen)
    for model in models:
        ctx.ui.status_text = tr(f"проверяю {model}", f"testing {model}")
        ctx.ui.invalidate()
        try:
            ok, detail = await pictures.probe(runtime, model)
        finally:
            ctx.ui.status_text = ""
        if ok is None:
            ctx.say(f"{model}: " + tr("проверка не удалась — ", "the test could not run — ") + detail, "warn")
        elif ok:
            ctx.say(f"{model}: " + tr("видит изображения — прочитала код с картинки.",
                                      "sees images — it read the code off the picture."), "success")
        else:
            ctx.say(f"{model}: " + tr("не видит изображения — ", "does not see images — ") + detail
                    + tr(" Дальше картинки для неё описывает модель для изображений.",
                         " From now on the image model describes pictures for it."), "warn")
    runtime.invalidate_prompt()

EFFORTS = [("auto", "Автоматически — как решит модель", "Automatic — the model decides"),
           ("off", "Выключить размышления", "Reasoning off"),
           ("on", "Включить размышления", "Reasoning on"),
           ("minimal", "Минимум — быстрее всего", "Minimal — fastest"),
           ("low", "Низкое", "Low"), ("medium", "Среднее", "Medium"), ("high", "Высокое — глубже думает", "High — thinks deeper"),
           ("xhigh", "Очень высокое", "Extra high"),
           ("max", "Максимум — для самых сложных задач", "Max — for the hardest problems")]

@command("effort", "Глубина рассуждений модели", "How hard the model thinks",
         hint="[auto|off|minimal|low|medium|high|xhigh|max]", category="model", while_busy=True)
async def effort(ctx: Context) -> None:
    from ... import reasoning_levels as levels
    typed = ctx.args.strip().lower()

    value = levels.ALIASES.get(typed, typed) if typed else ""
    runtime = ctx.runtime

    supported, how = runtime.router.effort_support(runtime.model)
    note = (tr(f"{runtime.model}: {how}.", f"{runtime.model}: {how}.") if supported else
            tr(f"{runtime.model} не поддерживает выбор уровня: {how}.",
               f"{runtime.model} does not support a reasoning level: {how}."))
    own = runtime.router.effort_options(runtime.model) if hasattr(runtime.router, "effort_options") else []
    rows = [row for row in EFFORTS if row[0] in own] if own else [row for row in EFFORTS if row[0] != "on"]
    if value not in [row[0] for row in EFFORTS]:
        current = str(ctx.settings.get("effort") or "auto")
        options = [dlg.Option(tr(ru, en), key, hint="✓" if key == current else "") for key, ru, en in rows]
        body = ctx.r.render(Text("  " + note, style="dim" if supported else "warn"))
        keys = [row[0] for row in rows]
        value = await ctx.select(tr("Уровень рассуждений", "Reasoning effort"), options, body=body,
                                 index=keys.index(current) if current in keys else 0)
        if not value:
            return
    ctx.settings.set("effort", value)
    runtime.invalidate_prompt()
    nearest = ""
    if own and value not in own:
        profile = {"kind": "toggle" if "on" in own or own == ["auto", "off"] else "levels",
                   "levels": [x for x in own if x in levels.DEPTHS], "can_off": "off" in own, "default": ""}
        nearest = levels.clamp(value, profile)
    if supported and nearest and nearest != "auto":
        ctx.say(tr(f"Усилие: {value} — у этой модели такого нет, будет «{nearest}»",
                   f"Effort: {value} — this model lacks it, «{nearest}» will be used"), "info")
    elif supported:
        ctx.say(tr(f"Усилие: {value}", f"Effort: {value}"), "success")
    else:
        ctx.say(tr(f"Усилие: {value} — сохранено, но на текущую модель не действует. {note}",
                   f"Effort: {value} — saved, but it has no effect on the current model. {note}"), "warn")

@command("think", "Включить или выключить режим размышлений (Alt+T)", "Toggle extended thinking (Alt+T)",
         aliases=("thinking",), hint="[on|off]", category="model", immediate=True, while_busy=True)
async def think(ctx: Context) -> None:
    arg = ctx.args.strip().lower()
    value = (arg in ("on", "вкл", "1", "true")) if arg else not bool(ctx.settings.get("thinking"))
    ctx.settings.set("thinking", value)
    ctx.say(tr("Размышления: ", "Thinking: ") + (tr("включены", "on") if value else tr("выключены", "off")), "success")

@command("web", "Встроенный веб-поиск провайдера вкл/выкл", "Toggle the provider's built-in web search", hint="[on|off]",
         category="model", immediate=True, while_busy=True)
async def web(ctx: Context) -> None:
    arg = ctx.args.strip().lower()
    value = (arg in ("on", "вкл", "1", "true")) if arg else not bool(ctx.settings.get("web_search"))
    ctx.settings.set("web_search", value)
    ctx.say(tr("Веб-поиск провайдера: ", "Provider web search: ") + (tr("вкл", "on") if value else tr("выкл", "off"))
            + tr(" (инструменты WebSearch/WebFetch доступны всегда)", " (WebSearch/WebFetch tools are always available)"),
            "success")

@command("advisor", "Советник — более сильная модель для второго мнения", "Advisor — a stronger model for a second opinion",
         hint="[model|off|question]", category="model", while_busy=True)
async def advisor(ctx: Context) -> None:
    runtime = ctx.runtime
    args = ctx.args.strip()
    if args.lower() in ("off", "выкл", "none"):
        ctx.settings.set("advisor_model", "")
        runtime.invalidate_prompt()
        ctx.say(tr("Советник выключен", "Advisor is off"), "success")
        return
    known = {model.id for model in runtime.router.cached_models()}
    if not args or args in known or (args.startswith("@") and ":" in args and " " not in args):
        chosen = args if args else await pick_model(ctx, tr("Модель-советник", "Advisor model"),
                                                    current=str(ctx.settings.get("advisor_model") or ""),
                                                    allow_none=tr("Без советника", "No advisor"))
        if chosen is None:
            return
        ctx.settings.set("advisor_model", chosen)
        runtime.invalidate_prompt()
        ctx.say(tr(f"Советник: {chosen or 'выключен'}. Агент сам спросит его в трудный момент; /advisor <вопрос> — спросить напрямую.",
                   f"Advisor: {chosen or 'off'}. The agent consults it on hard calls; /advisor <question> asks directly."),
                "success")
        return
    if not ctx.settings.get("advisor_model"):
        ctx.say(tr("Сначала выберите советника: /advisor", "Choose an advisor first: /advisor"), "warn")
        return
    ctx.ui.status_text = tr("советник думает", "advisor is thinking")
    try:
        answer = await runtime.consult_advisor(args)
    except Exception as exc:
        ctx.say(str(exc), "error")
        return
    finally:
        ctx.ui.status_text = ""
    ctx.print(Panel(ctx.r.md(answer), title=f"Advisor · {ctx.settings.get('advisor_model')}", title_align="left",
                    border_style="brand2"))
    runtime.session.append({"role": "user", "kind": "note", "content":
                            f"[The user asked the advisor: {args}]\n[Advisor answer]\n{answer[:8000]}"})
