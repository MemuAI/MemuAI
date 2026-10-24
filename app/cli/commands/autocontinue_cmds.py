"""/auto-continue — send "continue" by itself at a time (Round 33; the logic is in ``app/cli/autocontinue.py``)."""
from __future__ import annotations

import time
from typing import Any

from .. import autocontinue as ac
from rich.text import Text

from ..i18n import tr
from ..tui import dialogs as dlg
from . import Context, command


def manager(ui: Any) -> ac.AutoContinue:
    current = getattr(ui, "auto_continue", None)
    if not isinstance(current, ac.AutoContinue):
        current = ac.AutoContinue(ui)
        ui.auto_continue = current
    return current


def _armed_text(auto: ac.AutoContinue, extra: str = "") -> str:
    plan = auto.plan
    now = time.time()
    if plan is None:
        return ""
    head = tr(f"◷ Автопродолжение: «{plan.message[:60]}» в {ac.clock_text(plan.at, now)} "
              f"(через {ac.countdown(plan.at - now)})",
              f"◷ Auto-continue: “{plan.message[:60]}” at {ac.clock_text(plan.at, now)} "
              f"(in {ac.countdown(plan.at - now)})")
    tail = []
    if plan.retries:
        tail.append(tr(f"повторы при лимите: {plan.retries}", f"retries if still limited: {plan.retries}"))
    if plan.fallback:
        tail.append(tr(f"запасная модель: {plan.fallback}", f"fallback: {plan.fallback}"))
    tail.append(tr("окно должно оставаться открытым", "keep this window open"))
    tail.append("/auto-continue cancel")
    return head + (f" · {extra}" if extra else "") + "\n  " + " · ".join(tail)


async def _pick(ctx: Context, auto: ac.AutoContinue) -> Any:
    """No time given: a short menu (the provider's reset time first, when it is known)."""
    now = time.time()
    reset = float(auto.last_limit.get("at") or 0.0)
    options = []
    if reset > now:
        options.append(dlg.Option(tr("Когда сбросится лимит", "When the limit resets"), "auto",
                                  tr(f"в {ac.clock_text(reset, now)} — время назвал провайдер",
                                     f"at {ac.clock_text(reset, now)} — the time the provider named"),
                                  hint=ac.countdown(reset - now)))
    for label, seconds in (("15 min", 900), ("30 min", 1800), ("1 h", 3600), ("2 h", 7200), ("5 h", 18000)):
        options.append(dlg.Option(tr("Через ", "In ") + label, now + seconds, tr("в ", "at ") + ac.clock_text(now + seconds, now)))
    tomorrow = ac.parse_time("tomorrow 09:00", now)
    options.append(dlg.Option(tr("Завтра в 09:00", "Tomorrow at 09:00"), tomorrow))
    if reset <= now:
        options.append(dlg.Option(tr("Авто", "Auto"), "auto",
                                  tr("время сброса из ошибки лимита; иначе 15 мин и повторы",
                                     "the reset time from a limit error; otherwise 15 min, then retries")))
    options.append(dlg.Option(tr("Своё время…", "Other time…"), "custom", "18:30 · +2h · in 45m · tomorrow 09:00"))
    if auto.plan is not None:
        options.append(dlg.Option(tr("Отменить назначенное", "Cancel the one that is set"), "cancel",
                                  auto.describe(now), style="dialog.danger"))
    body = auto.describe(now) if auto.plan is not None else ""
    choice = await ctx.select(tr("Когда отправить «continue»?", "When should “continue” be sent?"), options,
                              body=ctx.r.render(Text("  " + body, style="dim")) if body else "")
    if choice == "custom":
        text = await ctx.ask(tr("Когда отправить?", "When should it be sent?"),
                             body="18:30 · 6:30pm · +2h · in 45m · 1h30m · tomorrow 09:00")
        if not text:
            return None
        try:
            return ac.parse_time(text)
        except ac.TimeError as exc:
            ctx.say(str(exc), "warn")
            return None
    return choice


@command("auto-continue", "Отправить «continue» само в указанное время — когда кончились лимиты: "
                          "/auto-continue 18:30 · +2h · auto · cancel",
         "Send “continue” by itself later — when the limits ran out: /auto-continue 18:30 · +2h · auto · cancel",
         aliases=("autocontinue", "continue-at"), hint="[time|duration|auto|status|cancel] [message]",
         category="session", while_busy=True)
async def auto_continue(ctx: Context) -> None:
    auto = manager(ctx.ui)
    request = ac.parse_args(ctx.args)
    if request.error:
        ctx.say(request.error, "warn")
        return
    if request.action == "help":
        ctx.md("```text\n" + ac.HELP + "\n```")
        return
    if request.action == "status":
        if auto.plan is None:
            ctx.say(auto.describe() + tr(" Назначить: /auto-continue 18:30 · +2h · auto",
                                         " Set one with /auto-continue 18:30 · +2h · auto"))
        else:
            ctx.say(_armed_text(auto))
        return
    if request.action == "cancel":
        if not auto.cancel():
            ctx.say(auto.describe())
        return
    if request.fallback:
        known = {getattr(item, "id", "") for item in ctx.runtime.router.cached_models()} \
            if hasattr(ctx.runtime.router, "cached_models") else set()
        if known and request.fallback not in known:
            ctx.say(tr(f"Модель {request.fallback} не найдена среди подключённых — проверьте /model.",
                       f"The model {request.fallback} is not among the connected models — check /model."), "warn")
            return
    if request.action == "now":
        if auto.plan is None:
            auto.arm(time.time(), message=request.message, retries=request.retries, fallback=request.fallback)
        else:
            auto.plan.at = time.time()
            if request.message:
                auto.plan.message, auto.plan.custom_message = request.message, True
            auto._persist()
        if ctx.ui.busy:
            ctx.say(tr("◷ Отправлю, как только закончится текущий ответ.", "◷ Will send as soon as the current answer ends."))
        return
    at: Any = request.at
    auto_mode = request.auto
    if request.action == "pick":
        at = await _pick(ctx, auto)
        if at is None:
            return
        if at == "cancel":
            auto.cancel()
            return
        auto_mode = at == "auto"
    if auto_mode:
        plan, known = auto.arm_auto(message=request.message, retries=request.retries, fallback=request.fallback)
        note = tr("время сброса назвал провайдер", "the provider named the reset time") if known else \
            tr("провайдер не назвал время сброса — пробую через 15 мин, потом с паузами",
               "the provider did not name a reset time — trying in 15 min, then backing off")
        ctx.say(_armed_text(auto, note), "success")
        return
    auto.arm(float(at), message=request.message, retries=request.retries, fallback=request.fallback)
    ctx.say(_armed_text(auto), "success")
