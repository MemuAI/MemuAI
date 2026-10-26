"""/help-ask — ask anything about MemuCode itself (Round 33; the reference is ``app/cli/selfdoc.py``)."""
from __future__ import annotations

from .. import selfdoc
from ..i18n import tr
from . import Context, command

def offline_markdown(question: str) -> str:
    lines = selfdoc.offline_answer(question)
    if not lines:
        return tr("В справочнике ничего не нашлось. Список команд: /help.",
                  "Nothing in the reference matches. The command list: /help.")
    return "\n".join(f"- {line}" for line in lines)

@command("help-ask", "Спросить что угодно о самом MemuCode — ответ по встроенному справочнику: /help-ask как включить "
                     "планирование?",
         "Ask anything about MemuCode itself — answered from its built-in reference: /help-ask how do I turn on "
         "planning?", aliases=("ask-memucode", "howto"), hint="<question>", category="general", while_busy=False)
async def help_ask(ctx: Context) -> None:
    words = ctx.args.strip()
    offline = False
    if words.startswith("--offline"):
        offline, words = True, words[len("--offline"):].strip()
    question = words or (await ctx.ask(tr("Что вы хотите узнать о MemuCode?", "What do you want to know about MemuCode?"),
                                       body="how do I turn on planning? · what does /compact do? · где настройки?") or "")
    question = question.strip()
    if not question:
        return
    runtime = ctx.runtime
    if offline or not runtime.model:
        if not runtime.model:
            ctx.say(tr("Модель не выбрана — отвечаю по справочнику без модели.",
                       "No model is selected — answering from the reference without a model."), "warn")
        ctx.md(offline_markdown(question))
        return
    session = runtime.session

    runtime.selfdoc_forced_for = (session.id, len(session.user_prompts()) + 1)
    await ctx.prompt(question, display=ctx.raw)
