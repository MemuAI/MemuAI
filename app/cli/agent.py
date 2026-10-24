"""The agent loop of MemuCode.

One user turn is a loop of model rounds: stream the answer, parse the tool
calls it ends with, run them (read-only ones in parallel), append the results
and ask the model again — until it answers without tools, the user presses
Escape, a denial asks to stop, or the round limit is reached.

The same class runs sub-agents (``Task``): a fresh in-memory session, a role
prompt, a restricted tool set and a relay UI that shows their activity under
the parent call.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import traceback
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from . import compact, convert, prompts, protocol
from . import vision as pictures
from .i18n import tr
from .llm import ChatRequest, LLMError, Usage
from .project import AgentDef
from .session import Session, new_id
from .tokens import estimate_messages, estimate_text
from .tools.base import Prepared, ToolCall, ToolContext, ToolError, ToolResult
from .ui_base import BaseUI

if TYPE_CHECKING:  # pragma: no cover
    from .runtime import Runtime

RESULT_CHARS = 60_000
MAX_INVALID_STREAK = 6          # unreadable tool calls in a row after which the turn is stopped
# Round 29 (r29c-prompts). Weak models (gpt-oss) sometimes end a round with an EMPTY reply in the middle of a task
# (after one or two reads) — the turn used to end silently with nothing done. They are nudged to go on, a bounded
# number of times per turn.
MAX_EMPTY_NUDGES = 2
EMPTY_REPLY_NUDGE = ("[Your last reply was empty. Continue the task: call the next tool you need, or, if the work is "
                     "finished, write the final answer for the user. Do not reply with an empty message.]")
# The same model also repeats an identical read-only call (the same Bash/Read/Grep…) while nothing in the project
# changed in between. Such a call is not run again: the model gets its earlier result back as a reminder, and a turn
# that keeps sending only repeats is asked for its final answer without tools.
DUPLICATE_GUARD_TOOLS = {"Read", "Glob", "Grep", "LS", "Outline", "Symbols", "ProjectInfo", "Git", "WebSearch",
                         "WebFetch", "Bash"}
MAX_DUPLICATE_ROUNDS = 3
SUBAGENT_DENY = ["Task", "Agents", "AskUserQuestion", "ExitPlanMode", "TodoWrite"]
# Round 34: a thinking model (GLM-5.3 via a gateway) wrote «Шаг 1 — добавляю median в calc.py…», put the edit into its
# reasoning only and ended the round without a call — the turn finished with nothing done. A reply whose last
# sentence announces the next action is asked, once per turn, to make that call (or to give the final answer).
MAX_PROMISE_NUDGES = 1
PROMISE_NUDGE = ("[You announced the next step but did not call a tool, so nothing was done. If the task is not "
                 "finished, call the tool for that step now. If it is finished, give the final answer.]")
_PROMISE = re.compile(
    r"(?:^|[\s—–:(-])(?:приступаю|перехожу|начинаю|добавляю|создаю|пишу|правлю|исправляю|"
    r"запускаю|обновляю|меняю|читаю|проверяю|смотрю|ищу|удаляю|добавлю|создам|напишу|исправлю|запущу|обновлю|"
    r"проверю|посмотрю|сделаю|начну|прочитаю|изучу|открою|поищу|выполню|внесу|допишу|перепишу|реализую|"
    r"let me(?=\s)(?!\s+(?:know|summari[sz]e|explain|clarify|recap|note))|i'll start|first,? i'll|starting with|moving on to|"
    r"(?:i'll|i will|i'm going to|now i|next,? i)(?:\s+(?:now|first|then|next))?\s+(?:read|run|check|look|open|create|"
    r"write|add|edit|update|fix|start|search|install|implement|modify|change|inspect|list|test|begin|try|apply|make|"
    r"rewrite|refactor|remove|delete|go|grep|build|call))(?=[\s,.:…!]|$)",
    re.IGNORECASE)
_FINISHED = re.compile(r"готово|сделано|заверш|всё выполнено|done\b|finished|completed|all set", re.IGNORECASE)
# …but not an offer, a condition or a plan for later («Если хотите, я добавлю типы», «Проверю при следующем запуске»,
# «Let me know…», «Запускать тесты не нужно») — those are ordinary final answers.
_NOT_NOW = re.compile(
    r"\bесли\b|\bкогда (?:вы|захотите)|хотите|захотите|скажите|при желании|при необходимости|можно|могу|могли бы|"
    r"в будущем|в следующий раз|при следующ|позже|не нужно|не надо|не требуется|не буду|не стану|"
    r"\bif\b|\bwhen you|would you|let me know|in the future|next time|later\b|no need|not needed|won't|"
    r"\bcan\b|\bcould\b",
    re.IGNORECASE)


def announces_next_step(text: str) -> bool:
    """True when the last sentence of ``text`` says what the agent is about to do («Теперь добавляю…», «Let me…»)."""
    tail = (text or "").strip()
    if not tail or tail.endswith("?"):
        return False
    sentences = [part for part in re.split(r"(?<=[.!…])\s+|\n+", tail[-400:]) if part.strip()]
    last = " ".join(sentences[-2:]) if sentences else tail       # «Шаг 1 — добавляю X. Для пустого списка — 0.»
    return bool(_PROMISE.search(last)) and not _FINISHED.search(last) and not _NOT_NOW.search(last)


@dataclass
class AssistantOutput:
    text: str = ""
    visible: str = ""
    reasoning: str = ""
    calls: List[Dict[str, Any]] = field(default_factory=list)
    parse_errors: List[str] = field(default_factory=list)
    problems: List[Dict[str, str]] = field(default_factory=list)     # calls that could not be run: unreadable / cut off
    usage: Usage = field(default_factory=Usage)
    finish: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0


@dataclass
class TurnResult:
    text: str = ""
    rounds: int = 0
    stopped: str = "done"            # done | interrupted | limit | error | denied | blocked
    error: str = ""
    usage: Usage = field(default_factory=Usage)
    tools: int = 0
    seconds: float = 0.0
    error_category: str = ""          # Round 32: app/cli/errors.py category of a model-request error
    retry_at: float = 0.0             # Round 33: when the provider said its limit ends (ratelimit.py), 0 = unknown


class EphemeralSession(Session):
    """A sub-agent conversation: kept in memory, shares the parent id so its
    edits land in the same checkpoints and sandbox."""

    def __init__(self, parent: Session) -> None:
        super().__init__(parent.root, parent.id)
        self.parent = parent
        self.cwd = parent.cwd
        self.image_notes = parent.image_notes            # pictures described for a sub-agent are described for all

    def _append(self, event: Dict[str, Any]) -> None:
        return None

    def note_edit(self, path: str, text: str) -> None:
        self.parent.note_edit(path, text)

    def add_image_note(self, key: str, text: str, model: str = "") -> None:
        self.parent.add_image_note(key, text, model)


def model_text(result: ToolResult) -> str:
    """What the model receives for one tool result."""
    if result.ok:
        output = result.output
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, indent=1)
    else:
        text = "Error: " + (result.error or "the tool failed")
        if result.output:
            body = result.output if isinstance(result.output, str) else json.dumps(result.output, ensure_ascii=False)
            if body.strip() and body.strip() not in text:
                text += "\n" + body
    if len(text) > RESULT_CHARS:
        head = text[:RESULT_CHARS * 2 // 3]
        tail = text[-RESULT_CHARS // 4:]
        text = f"{head}\n…[{len(text) - len(head) - len(tail)} characters omitted]…\n{tail}"
    return text


class Agent:
    def __init__(self, runtime: "Runtime", *, session: Optional[Session] = None, ui: Optional[BaseUI] = None,
                 agent_def: Optional[AgentDef] = None, depth: int = 0, model: str = "") -> None:
        self.runtime = runtime
        self.session = session or runtime.session
        self.ui: BaseUI = ui or runtime.ui
        self.agent_def = agent_def
        self.depth = depth
        self.model_override = model
        self.stop_after_round = False
        self.feedback: List[str] = []
        self.current_calls: List[ToolCall] = []
        self.invalid_streak = 0                 # rounds in a row whose calls could not be read at all
        self.label = agent_def.name if agent_def else "main"
        #: Round 32: follow-up instructions from the main agent for a running sub-agent (BackgroundJobs.send);
        #: delivered at the next turn boundary — after the tool results of the current step.
        self.inbox: List[str] = []

    # ------------------------------------------------------------ setup
    @property
    def model(self) -> str:
        return self.model_override or self.runtime.model

    @property
    def is_main(self) -> bool:
        return self.depth == 0

    def native(self) -> bool:
        return self.runtime.router.uses_native_tools(self.model)

    def tools(self):
        allow = None
        deny: List[str] = []
        if self.agent_def is not None:
            allow = list(self.agent_def.tools) if self.agent_def.tools else None
            deny = list(SUBAGENT_DENY)
        return self.runtime.tools.available(self.runtime, allow=allow, deny=deny)

    def build_request(self) -> Tuple[ChatRequest, str, str]:
        tools = self.tools()
        native = self.native()
        system = self.runtime.system_prompt(tools, native=native, agent=self.agent_def)
        # A model that is known not to see gets what an image model wrote about each picture instead of the picture;
        # one that may see (or nobody knows) gets the picture, and a refusal is handled in _request_round.
        sees = pictures.support(self.runtime, self.model).can is not False
        notes = None if sees else self.session.image_notes
        messages = list(self.session.api_messages())
        reminder = prompts.reminders(self.runtime) if self.is_main else ""
        converted = convert.to_native(messages, vision=sees, notes=notes) if native else \
            convert.to_text_protocol(messages, vision=sees, notes=notes)
        if reminder and converted:
            last = converted[-1]
            if last.get("role") == "user":
                if isinstance(last.get("content"), str):
                    last["content"] = last["content"] + "\n\n" + reminder
                elif isinstance(last.get("content"), list):
                    last["content"] = [*last["content"], {"type": "text", "text": reminder}]
            else:
                converted.append({"role": "user", "content": reminder})
        schemas = [tool.schema() for tool in tools] if native else None
        settings = self.runtime.settings
        request = ChatRequest(
            model=self.model, system=system, messages=converted, tools=schemas,
            effort=str(settings.get("effort") or "auto"), thinking=bool(settings.get("thinking")),
            web_search=bool(settings.get("web_search")), max_tokens=int(settings.get("max_output_tokens") or 0),
            temperature=settings.get("temperature"), session=self.session.id,
            show_thinking=bool(settings.get("show_thinking")))
        tools_text = json.dumps(schemas, ensure_ascii=False) if schemas else ""
        return request, system, tools_text

    # ------------------------------------------------------------ turn
    async def run_turn(self, content: Any, *, kind: str = "prompt", display: str = "",
                       extra: Optional[Dict[str, Any]] = None) -> TurnResult:
        started = time.monotonic()
        turn = new_id("turn")
        result = TurnResult()
        self.first_round = True
        self.partial_text = ""
        if kind == "prompt":
            self.stuck = None          # Round 29: a new request from the user starts a fresh stuck detector
        self._overflow_recovered = False
        if self.is_main:
            from .tools import connected
            await connected.refresh(self.runtime)
        if self.is_main and kind in ("prompt", "command"):
            outcome = await self.runtime.hooks.run("UserPromptSubmit", {"prompt": _plain(content)})
            for message in outcome.messages:
                self.ui.notice(message, "warn")
            if outcome.decision == "block" or outcome.stop:
                result.stopped = "blocked"
                result.error = outcome.reason or outcome.stop_reason or tr("Запрос остановлен хуком",
                                                                          "The prompt was blocked by a hook")
                self.ui.notice(result.error, "warn")
                return result
            if outcome.context:
                content = _with_context(content, outcome.context)
        message = {"role": "user", "kind": kind, "content": content, "turn": turn}
        if display:
            message["display"] = display
        if extra:
            message.update(extra)
        self.session.append(message)
        self.ui.turn_started(self, message)
        try:
            result = await self._loop(turn, result)
        except asyncio.CancelledError:
            self._close_pending(tr("Прервано пользователем", "Interrupted by the user"))
            partial = protocol.parse(self.partial_text).text if self.partial_text.strip() else ""
            if partial.strip():
                self.session.append({"role": "assistant", "content": partial + "\n\n[interrupted]", "turn": turn,
                                     "model": self.model, "interrupted": True})
            self.session.append({"role": "user", "kind": "interrupt", "turn": turn,
                                 "content": "[The user interrupted this response. Wait for their next message.]"})
            result.stopped = "interrupted"
        except LLMError as exc:
            result.stopped = "error"
            # Round 32: the real upstream text, status, provider and model, plus plain words and advice.
            from .errors import describe, info_of
            info = self.runtime.router.info(self.model)
            result.error = describe(exc, model=self.model,
                                    provider=str(getattr(exc, "provider", "") or (info.provider if info else "")))
            result.error_category = info_of(exc).category
            try:
                from .ratelimit import reset_at
                result.retry_at = reset_at(exc)
            except Exception:
                result.retry_at = 0.0
            self._close_pending(str(getattr(exc, "raw_message", exc)))
        except Exception as exc:  # a bug must not kill the session
            result.stopped = "error"
            result.error = f"{type(exc).__name__}: {exc}"
            self.runtime.log_error(traceback.format_exc())
            self._close_pending(result.error)
        result.seconds = time.monotonic() - started
        if self.is_main:
            try:
                self.session.flush_index()
            except Exception:
                pass
        self.ui.turn_finished(self, result)
        return result

    def _close_pending(self, reason: str) -> None:
        """Every issued call must get a result, or the next request is malformed.

        Results of a round are appended together when the round ends, so after
        an interruption none of the round's calls has one yet: finished calls
        keep their real result, the rest are marked as interrupted."""
        for call in self.current_calls:
            if call.result is not None:
                content, is_error = model_text(call.result), not call.result.ok
            else:
                call.status = "cancelled"
                content, is_error = f"Error: {reason}", True
            self.session.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content,
                                 "is_error": is_error, "args": call.args, "display": call.display})
        self.current_calls = []

    def _call_key(self, raw: Dict[str, Any]) -> str:
        args = raw.get("args") if isinstance(raw.get("args"), dict) else {}
        try:
            return str(raw.get("name") or "") + json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:
            return ""

    def _mark_duplicates(self, calls: List[Dict[str, Any]], guard: Dict[str, Any]) -> bool:
        """Marks repeated read-only calls (nothing non-passive succeeded since the identical call ran).
        True when the round consists of repeats only."""
        # Elision/compaction explicitly tells the model to re-read anything it still
        # needs. Results remembered before that boundary are no longer in context.
        if guard.get("context_epoch", self.session.context_epoch) != self.session.context_epoch:
            guard["seen"].clear()
            guard["dup_rounds"] = 0
        guard["context_epoch"] = self.session.context_epoch
        real = 0
        repeats = 0
        for raw in calls:
            name = str(raw.get("name") or "")
            if name == "__invalid__":
                continue
            real += 1
            tool = self.runtime.tools.resolve(name)
            canonical = tool.name if tool else name
            if canonical not in DUPLICATE_GUARD_TOOLS:
                continue
            key = self._call_key(dict(raw, name=canonical))
            seen = guard["seen"].get(key) if key else None
            if (seen is not None and seen["epoch"] == guard["epoch"]
                    and (seen.get("ok", True) or seen.get("failures", 0) >= 2)):
                raw["duplicate_of"] = seen["round"]
                repeats += 1
        return bool(real) and repeats == real

    def _note_executed(self, results: List[Tuple[ToolCall, ToolResult]], guard: Dict[str, Any], round_no: int) -> None:
        guard["context_epoch"] = self.session.context_epoch
        changed = any(result.ok and call.name not in DUPLICATE_GUARD_TOOLS - {"Bash"}
                      and call.name not in ("BashOutput", "TaskOutput", "TodoWrite", "__invalid__")
                      and not getattr(call, "duplicate_of", None)
                      for call, result in results)
        if changed:
            guard["epoch"] += 1
        for call, result in results:
            if call.name in DUPLICATE_GUARD_TOOLS and not getattr(call, "duplicate_of", None):
                key = self._call_key({"name": call.name, "args": call.args})
                if key:
                    previous = guard["seen"].get(key) or {}
                    failures = previous.get("failures", 0) if previous.get("epoch") == guard["epoch"] else 0
                    # A transient failure gets one real retry (e.g. a background
                    # generator has not created the file yet), never 80 paid rounds.
                    guard["seen"][key] = {"epoch": guard["epoch"], "round": round_no,
                                          "ok": result.ok, "failures": 0 if result.ok else failures + 1}

    async def _loop(self, turn: str, result: TurnResult) -> TurnResult:
        settings = self.runtime.settings
        limit = int(settings.get("max_turns") or 80) if self.is_main else 40
        guard: Dict[str, Any] = {"seen": {}, "epoch": 0, "dup_rounds": 0, "empty_nudges": 0, "promise_nudges": 0}
        while True:
            result.rounds += 1
            if result.rounds > limit:
                more = self.is_main and self.ui.interactive and await self.ui.confirm(
                    tr(f"Агент сделал уже {limit} раундов инструментов. Продолжить?",
                       f"The agent has done {limit} tool rounds. Continue?"),
                    tr("Он не зациклился, но задача оказалась длинной.", "It is not stuck, the task is just long."))
                if not more:
                    result.stopped = "limit"
                    self.session.append({"role": "user", "kind": "note", "turn": turn, "content": (
                        "[Round limit reached: summarise what is done and what remains; do not call tools.]")})
                    output = await self._request_round(final=True)
                    output.calls = []
                    result.usage.add(output.usage)
                    if not output.visible.strip():
                        output.visible = tr("Достигнут предел раундов инструментов; задача не завершена.",
                                            "The tool round limit was reached; the task is not finished.")
                    self._record_assistant(output, turn)
                    result.text = output.visible
                    return result
                limit += int(settings.get("max_turns") or 80)
            request: Optional[ChatRequest] = None
            await self._prepare_images()
            if self.is_main:
                request, system, tools_text = self.build_request()
                if await compact.optimize(self.runtime, system, tools_text):
                    request = None                 # the history changed: rebuild
            try:
                output = await self._request_round(request=request)
            except LLMError as exc:
                if not (self.is_main and context_overflow(exc) and await self._recover_overflow(exc)):
                    raise
                result.rounds -= 1
                continue
            result.usage.add(output.usage)
            self._record_assistant(output, turn)
            # The round's text is in the session now: an Escape during its tools must not save it a second time
            # as an "[interrupted]" answer (the transcript showed the same paragraph twice).
            self.partial_text = ""
            calls = list(output.calls)
            if output.problems:
                # Rounds in a row that brought nothing but unreadable calls: the advice gets blunter, and a model
                # that cannot get out of it is stopped instead of burning the whole round budget.
                self.invalid_streak = self.invalid_streak + 1 if not output.calls else 1
            else:
                self.invalid_streak = 0
            for problem in output.problems:
                # Every call that was not run still gets a result, next to the results of the good ones: the model
                # learns exactly which call failed and why, and only that call has to be sent again.
                calls.append({"id": new_id("t"), "name": "__invalid__",
                              "args": dict(problem, streak=str(self.invalid_streak))})
            if not calls:
                result.text = output.visible
                if self._inject_inbox(turn) and result.rounds < limit:
                    continue                       # a follow-up arrived while it was answering: act on it
                if not output.visible.strip() and guard["empty_nudges"] < MAX_EMPTY_NUDGES and result.rounds < limit:
                    guard["empty_nudges"] += 1
                    self.session.append({"role": "user", "kind": "note", "turn": turn, "content": EMPTY_REPLY_NUDGE})
                    continue
                if (guard["promise_nudges"] < MAX_PROMISE_NUDGES and result.rounds < limit
                        and announces_next_step(output.visible)):
                    guard["promise_nudges"] += 1
                    self.session.append({"role": "user", "kind": "note", "turn": turn, "content": PROMISE_NUDGE})
                    continue
                if self.is_main:
                    outcome = await self.runtime.hooks.run("Stop", {"stop_hook_active": result.rounds > 1,
                                                                    "last_assistant_message": output.visible[-4000:]})
                    if outcome.decision == "block" and outcome.reason and result.rounds < limit:
                        self.session.append({"role": "user", "kind": "hook", "turn": turn,
                                             "content": f"[Stop hook feedback] {outcome.reason}"})
                        continue
                    queued = self.ui.take_queued()
                    if queued:
                        self._append_queued(queued, turn)
                        continue
                    if self._inject_notes(turn):
                        continue                   # a background task finished meanwhile: react to it
                return result
            if self._mark_duplicates(calls, guard):
                guard["dup_rounds"] += 1
                # Round 29: when the user can answer, the stuck detector's pause owns this loop (the repeats are
                # still answered «not run again»); the hard stop stays for headless runs, sub-agents and «off».
                if guard["dup_rounds"] >= MAX_DUPLICATE_ROUNDS and not self._stuck_pause_owns_repeats():
                    self._answer_unrun(calls, turn, "identical to an earlier call — not run again.")
                    self.session.append({"role": "user", "kind": "note", "turn": turn, "content": (
                        "[Loop guard: you keep repeating tool calls whose results you already have. Do not call "
                        "tools now: answer the user from the results above, and say plainly what could not be done.]")})
                    output = await self._request_round(final=True)
                    output.calls = []
                    if not output.visible.strip():
                        output.visible = tr("Агент повторял одни и те же вызовы инструментов и был остановлен; "
                                            "задача не завершена.",
                                            "The agent kept repeating the same tool calls and was stopped; "
                                            "the task is not finished.")
                    result.stopped = "stuck"
                    result.usage.add(output.usage)
                    self._record_assistant(output, turn)
                    result.text = output.visible
                    return result
            else:
                guard["dup_rounds"] = 0
            # Round 29: the stuck detector pauses a looping agent for the user's verdict (app/stuck_detector.py).
            verdict = await self._stuck_gate(calls, turn)
            if verdict == "stop":
                result.stopped = "stuck"
                result.error = tr("Агент зациклился — остановлен по вашему решению",
                                  "The agent got stuck and was stopped as you decided")
                return result
            if verdict == "skip":
                continue
            results = await self.execute(calls, turn)
            self._note_executed(results, guard, result.rounds)
            self._inject_inbox(turn)
            if getattr(self, "stuck", None) is not None:
                from ..stuck_detector import cli_outcome
                self.stuck.note_result(**cli_outcome(results))
            result.tools += len([1 for call, _outcome in results if call.name != "__invalid__"])
            if self.invalid_streak >= MAX_INVALID_STREAK:
                result.stopped = "error"
                result.error = tr(
                    f"Модель {self.model} {self.invalid_streak} раз подряд прислала вызовы инструментов, которые "
                    "нельзя прочитать. Я остановил работу, чтобы не тратить лимиты впустую: повторите запрос или "
                    "выберите другую модель (/model).",
                    f"{self.model} sent tool calls that cannot be read {self.invalid_streak} times in a row. I stopped "
                    "so the limits are not wasted: send the request again or pick another model (/model).")
                return result
            if self.stop_after_round:
                self.stop_after_round = False
                result.stopped = "denied"
                feedback = "\n".join(self.feedback)
                self.feedback = []
                if feedback:
                    self.session.append({"role": "user", "kind": "prompt", "turn": turn, "content": feedback})
                    continue
                return result
            if self.is_main:
                queued = self.ui.take_queued()
                if queued:
                    self._append_queued(queued, turn)
                self._inject_notes(turn)

    def _stuck_pause_owns_repeats(self) -> bool:
        """True when a looping turn will be paused for the user (interactive main agent, detector not off)."""
        if not (self.is_main and getattr(self.ui, "interactive", False)):
            return False
        from .. import stuck_detector as sd
        sens = self.runtime.settings.get("stuck_sensitivity") or self.runtime.memuai_prefs().get(sd.PREF_KEY)
        return sd.normalize_sensitivity(sens) != "off"

    async def _stuck_gate(self, calls: List[Dict[str, Any]], turn: str) -> str:
        """``run`` — execute the round; ``skip`` — the user gave a hint / a new approach, the calls are answered
        with that note instead of being run; ``stop`` — the turn ends. Nothing runs while the question is open."""
        from .. import stuck_detector as sd
        sens = self.runtime.settings.get("stuck_sensitivity") or self.runtime.memuai_prefs().get(sd.PREF_KEY)
        detector = getattr(self, "stuck", None)
        if detector is None or detector.sensitivity != sd.normalize_sensitivity(sens):
            detector = self.stuck = sd.StuckDetector(sens, area="cli")
        if not detector.enabled:
            return "run"
        if not any(str(c.get("name") or "") != "__invalid__" for c in calls):
            return "run"   # only unreadable calls: MAX_INVALID_STREAK owns that loop (stops with a reason)
        step = sd.cli_round(calls)
        verdict = detector.observe(step["action"], label=step["label"], passive=step["passive"])
        if verdict.level == "hint":
            self.session.append({"role": "user", "kind": "note", "turn": turn,
                                 "content": f"[Stuck detector] {verdict.model_hint}"})
            return "run"
        if not verdict.paused:
            return "run"
        lang = "en" if tr("ru", "en") == "en" else "ru"
        card = verdict.to_card()
        answer = None
        if self.is_main and getattr(self.ui, "interactive", False):
            answer = await self.ui.ask_question(sd.cli_text(card, lang), sd.choices(lang))
        else:
            self.ui.notice(sd.telegram_text(card, lang), "warn")
        if answer is None:
            # Nobody can answer (headless, a sub-agent): end the turn instead of looping on.
            self.session.append({"role": "user", "kind": "note", "turn": turn, "content": sd.model_note("stop", "", verdict)})
            self._answer_unrun(calls, turn, sd.model_note("stop", "", verdict))
            return "stop"
        decision = sd.parse_decision(answer)
        if str(answer).strip() == sd.choices(lang)[1]:
            hint = await self.ui.ask_question(tr("Подсказка для модели:", "Hint for the model:"), [])
            decision = {"decision": "hint", "hint": str(hint or "").strip()} if hint else {"decision": "continue", "hint": ""}
        choice, hint = decision["decision"], decision.get("hint", "")
        detector.acknowledge(choice)
        note = sd.model_note(choice, hint, verdict)
        if choice == "continue":
            self.session.append({"role": "user", "kind": "note", "turn": turn, "content": note})
            return "run"
        self._answer_unrun(calls, turn, note + " These tool calls were NOT executed.")
        if choice == "stop":
            return "stop"
        self.session.append({"role": "user", "kind": "prompt", "turn": turn, "content": note})
        return "skip"

    def _answer_unrun(self, calls: List[Dict[str, Any]], turn: str, text: str) -> None:
        """Every issued call must get a result, or the next request is malformed."""
        for raw in calls:
            self.session.append({"role": "tool", "tool_call_id": str(raw.get("id") or new_id("t")),
                                 "name": str(raw.get("name") or ""), "content": f"Error: {text}", "is_error": True,
                                 "turn": turn, "args": _slim_args(raw.get("args") if isinstance(raw.get("args"), dict) else {}),
                                 "display": ""})

    async def _recover_overflow(self, exc: Optional[BaseException] = None) -> bool:
        """The provider said the request does not fit the context window (our estimate was too low — another
        tokenizer, a huge tool result). Shrink the history once per turn — elide old results, then compact —
        and let the round be asked again; a second overflow in the same turn is reported as it is."""
        if getattr(self, "_overflow_recovered", False):
            return False
        self._overflow_recovered = True
        from . import errors as _errors
        category = _errors.info_of(exc).category if exc is not None else "context_overflow"
        reason = {"too_many_messages": "The conversation has too many messages for the Proxy",
                  "payload_too_large": "The request body is too large"}.get(
                      category, "The request did not fit the model's context")
        self.ui.notice(tr("Запрос не поместился — сжимаю историю и повторяю…",
                          f"{reason} — compacting and retrying…"), "warn")
        elided = compact.elide_old_results(self.runtime, force=True)
        try:
            outcome = await compact.compact(self.runtime, reason="overflow")
        except Exception as exc:  # the summary request itself may overflow or fail
            self.runtime.log_error(f"overflow compaction failed: {exc}")
            outcome = {"ok": False}
        if outcome.get("ok"):
            self.ui.compacted(outcome)
        return bool(elided or outcome.get("ok"))

    async def _prepare_images(self) -> bool:
        """Pictures in the conversation that the current model cannot see are described by the image model — by
        itself, without asking, announced in one line. True when something was described."""
        runtime = self.runtime
        messages = self.session.api_messages()
        if not pictures.has_images(messages):
            return False
        if pictures.support(runtime, self.model).can is not False or pictures.mode(runtime) == "off":
            return False
        items = pictures.pending(self.session, messages)
        if not items:
            return False
        return bool(await pictures.describe(runtime, self.ui, items, main_model=self.model))

    def _inject_inbox(self, turn: str) -> bool:
        """Follow-ups the main agent sent to this (sub-)agent while it worked: one user message, between steps."""
        if self.is_main or not self.inbox:
            return False
        items = list(self.inbox)
        del self.inbox[:]
        self.session.append({"role": "user", "kind": "note", "turn": turn, "content": (
            "[Follow-up instructions from the main agent — they take priority over the original task where they "
            "differ]\n" + "\n\n".join(items))})
        self.ui.status(f"follow-up received ({len(items)})")
        return True

    def _inject_notes(self, turn: str) -> bool:
        """Results of finished background agents/commands reach the model between two of its steps —
        after the tool results, never between a tool call and its result."""
        if not self.is_main:
            return False
        notes = self.runtime.drain_notes()
        if not notes:
            return False
        self.session.append({"role": "user", "kind": "note", "turn": turn, "content": "\n\n".join(notes)})
        self.ui.notice(f"Background results delivered ({len(notes)})", "info")
        return True

    def _append_queued(self, queued: List[Dict[str, Any]], turn: str) -> None:
        for item in queued:
            # "shown" is the message as the conversation prints it (pasted text in full, no input chips).
            display = item.get("shown") or item.get("display", "")
            self.session.append({"role": "user", "kind": "prompt", "turn": turn, "queued": True,
                                 "content": item.get("content"), "display": display})
            self.ui.turn_started(self, {"role": "user", "content": item.get("content"),
                                        "display": display, "queued": True})

    def _record_assistant(self, output: AssistantOutput, turn: str) -> Dict[str, Any]:
        message: Dict[str, Any] = {"role": "assistant", "content": output.visible, "turn": turn,
                                   "model": self.model, "usage": output.usage.to_dict()}
        if output.calls:
            message["tool_calls"] = [{"id": call["id"], "name": call["name"], "args": call.get("args") or {}}
                                     for call in output.calls]
        if output.reasoning:
            message["reasoning"] = output.reasoning[-20000:]
        if output.extra.get("anthropic_content"):
            message["_anthropic"] = {"model": output.extra.get("model"), "content": output.extra["anthropic_content"]}
        return self.session.append(message)

    # ------------------------------------------------------------ model
    async def _request_round(self, *, final: bool = False, request: Optional[ChatRequest] = None) -> AssistantOutput:
        if request is None:
            request, _system, _tools_text = self.build_request()
        backend, raw_model = self.runtime.router.resolve(self.model)
        request.model = raw_model
        native = self.native()
        if final:
            self._no_tools(request, native)
        output = AssistantOutput()
        hider = protocol.StreamHider()
        texts: List[str] = []
        reasoning: List[str] = []
        started = time.monotonic()
        attempt = 0
        self.partial_text = ""
        self.ui.stream_started(self)
        self.ui.stream_status(self, "request", {"model": self.model, "tokens": estimate_messages(request.messages)
                                                + estimate_text(request.system)})
        announced = (0, 0.0)                    # (hidden characters, time) of the last "writing a tool call" notice
        sent_images = pictures.request_has_images(request.messages)
        was_unknown = sent_images and pictures.support(self.runtime, self.model).can is None
        retried_images = False
        while True:
            try:
                async for event in backend.stream(request):
                    if event.kind == "text":
                        texts.append(event.text)
                        self.partial_text += event.text
                        visible = event.text if native else hider.feed(event.text)
                        if visible:
                            self.ui.stream_text(self, visible)
                        if not native and hider.hidden_chars != announced[0]:
                            now = time.monotonic()
                            if now - announced[1] > 0.12 or hider.hidden_chars - announced[0] > 400:
                                announced = (hider.hidden_chars, now)
                                self.ui.stream_block(self, hider.peek, hider.hidden_chars)
                    elif event.kind == "reasoning":
                        reasoning.append(event.text)
                        self.ui.stream_reasoning(self, event.text)
                    elif event.kind == "tool_call" and event.call:
                        output.calls.append(event.call)
                        self.ui.stream_block(self, str(event.call.get("name") or ""), 0)
                    elif event.kind in ("accepted", "alive"):
                        self.ui.stream_status(self, event.kind, event.data or {})
                    elif event.kind == "usage" and event.usage:
                        output.usage = event.usage
                    elif event.kind == "status":
                        self.ui.status(event.text)
                    elif event.kind == "search":
                        sources = event.data.get("sources") if isinstance(event.data, dict) else None
                        if sources:
                            self.ui.notice(tr(f"Провайдер нашёл источников: {len(sources)}",
                                              f"The provider searched the web: {len(sources)} sources"))
                    elif event.kind == "done":
                        output.finish = event.finish
                        output.extra.update(event.data or {})
                break
            except LLMError as exc:
                if sent_images and not retried_images and not texts and not output.calls and pictures.refused_images(exc):
                    # The model (or the API behind it) would not take the picture although nothing said so
                    # beforehand: remember that, let the image model describe it, and ask again.
                    retried_images = True
                    pictures.learn(self.model, False, str(exc))
                    self.runtime.invalidate_prompt()
                    if pictures.mode(self.runtime) != "off":
                        await self._prepare_images()
                    request, _system, _tools_text = self.build_request()
                    request.model = raw_model
                    if final:
                        self._no_tools(request, native)
                    sent_images = pictures.request_has_images(request.messages)
                    was_unknown = False
                    self.ui.status(tr("Модель не приняла изображение — пробую с описанием",
                                      "The model did not take the picture — trying with a description"))
                    continue
                if attempt < 2 and not texts and not output.calls and (exc.retryable or _retryable(exc)):
                    attempt += 1
                    self.ui.status(tr(f"Повтор запроса ({attempt}/2)…", f"Retrying ({attempt}/2)…"))
                    await asyncio.sleep(2.0 * attempt)
                    continue
                raise
        if not native:
            tail = hider.flush()
            if tail:
                self.ui.stream_text(self, tail)
        output.seconds = time.monotonic() - started
        output.text = "".join(texts)
        output.reasoning = "".join(reasoning)
        if native:
            output.visible = output.text.strip()
            used = {str(call.get("id")) for message in self.session.messages if message.get("role") == "assistant"
                    for call in message.get("tool_calls") or []}
            for call in output.calls:
                # Some OpenAI-compatible servers number calls "call_0", "call_1" afresh every response; a reused id
                # would pair a result with the wrong call (and Anthropic rejects duplicate tool_use ids).
                if not call.get("id") or str(call["id"]) in used:
                    call["id"] = new_id("t")
                used.add(str(call["id"]))
        else:
            parsed = protocol.parse(output.text, known_keys=self.runtime.tools.argument_names(),
                                    finished=output.finish in ("stop", "end_turn", "stop_sequence", "tool_calls"))
            output.visible = parsed.text
            output.calls = [{"id": call.id, "name": call.name, "args": call.args, "notes": call.notes}
                            for call in parsed.calls]
            output.parse_errors = parsed.errors
            limit_hit = output.finish in ("length", "max_tokens")
            for name in parsed.cut_off:
                output.problems.append({"kind": "cut_off", "tool": name, "limit": "1" if limit_hit else ""})
            for message in parsed.errors:
                output.problems.append({"kind": "invalid", "error": message})
        if was_unknown and (output.text.strip() or output.calls):
            pictures.learn(self.model, True, "a request with a picture was answered")
        if not output.usage.input and not output.usage.output:
            output.usage = Usage(input=estimate_messages(request.messages) + estimate_text(request.system),
                                 output=estimate_text(output.text) + estimate_text(output.reasoning),
                                 estimated=True, requests=1)
        output.usage.requests = output.usage.requests or 1
        if self.is_main:
            # What the provider itself counted for this request: /context and the footer show this
            # number (plus what was added since) instead of an estimate from text length.
            self.runtime.context_measured = None if output.usage.estimated or not output.usage.input else {
                "input": int(output.usage.input), "output": int(output.usage.output), "model": self.model,
                "messages": len(self.session.api_messages()), "epoch": self.session.context_epoch}
        info = self.runtime.router.info(self.model)
        self.runtime.stats.add_usage(self.model, output.usage, provider=info.provider if info else "",
                                     seconds=output.seconds)
        self.runtime.record_round(self, output)
        self.ui.stream_finished(self, output.visible, output.reasoning, output.usage)
        return output

    @staticmethod
    def _no_tools(request: ChatRequest, native: bool) -> None:
        """A round that must be answered in words. A native conversation keeps its tool declarations — the
        history holds tool calls, and Anthropic rejects tool_use blocks in a request that declares no tools —
        and asks for no call instead."""
        if native and request.tools:
            request.tool_choice = "none"
        else:
            request.tools = None

    # ------------------------------------------------------------ tools
    async def execute(self, calls: List[Dict[str, Any]], turn: str) -> List[Tuple[ToolCall, ToolResult]]:
        items: List[ToolCall] = []
        for raw in calls:
            tool = self.runtime.tools.resolve(str(raw.get("name") or ""))
            call = ToolCall(name=tool.name if tool else str(raw.get("name") or ""),
                            args=raw.get("args") if isinstance(raw.get("args"), dict) else {},
                            id=str(raw.get("id") or new_id("t")), raw_name=str(raw.get("name") or ""))
            call.notes = list(raw.get("notes") or [])
            if raw.get("duplicate_of"):
                call.duplicate_of = int(raw["duplicate_of"])   # Round 29: a repeat that is answered, not run
            call.display = tool.display(call.args) if tool else ""
            items.append(call)
        self.current_calls = list(items)
        results: List[Optional[ToolResult]] = [None] * len(items)
        index = 0
        allowed_names = {tool.name for tool in self.tools()}
        while index < len(items):
            tool = self.runtime.tools.resolve(items[index].name)
            if tool is not None and tool.parallel and tool.name in allowed_names:
                group = [index]
                while (index + len(group) < len(items) and len(group) < 8):
                    nxt = self.runtime.tools.resolve(items[index + len(group)].name)
                    if nxt is None or not nxt.parallel or nxt.name not in allowed_names:
                        break
                    group.append(index + len(group))
                outcomes = await asyncio.gather(*(self._run_one(items[i], turn, allowed_names) for i in group))
                for position, outcome in zip(group, outcomes):
                    results[position] = outcome
                index += len(group)
            else:
                results[index] = await self._run_one(items[index], turn, allowed_names)
                index += 1
        pairs: List[Tuple[ToolCall, ToolResult]] = []
        for call, result in zip(items, results):
            assert result is not None
            pairs.append((call, result))
            message: Dict[str, Any] = {"role": "tool", "tool_call_id": call.id, "name": call.name,
                                       "content": model_text(result), "is_error": not result.ok, "turn": turn,
                                       "args": _slim_args(call.args), "display": call.display,
                                       "summary": result.summary}
            if result.images:
                message["images"] = result.images
                if result.meta.get("question"):
                    message["image_question"] = str(result.meta["question"])[:800]
            if result.diff:
                message["diff"] = result.diff[:20000]
            self.session.append(message)
        self.current_calls = []
        return pairs

    async def _run_one(self, call: ToolCall, turn: str, allowed: set) -> ToolResult:
        runtime = self.runtime
        call.started = time.monotonic()
        tool = runtime.tools.resolve(call.name)
        if call.name == "__invalid__":
            result = ToolResult.fail(_invalid_text(call.args))
            result.summary = (tr("Вызов оборвался и не выполнен — модель пришлёт его заново",
                                 "Call was cut off, not run — the model will resend it")
                              if call.args.get("kind") == "cut_off"
                              else tr("Вызов не прочитан — модель пришлёт его заново",
                                      "Call could not be read — the model will resend it"))
            call.status = "error"
            self.ui.tool_started(self, call)
            self.ui.tool_finished(self, call, result)
            return result
        if tool is None or tool.name not in allowed or not tool.enabled(runtime):
            names = ", ".join(sorted(allowed))
            result = ToolResult.fail(f"Unknown or unavailable tool {call.raw_name or call.name!r}. "
                                     f"Available tools: {names}")
            call.status = "error"
            self.ui.tool_started(self, call)
            self.ui.tool_finished(self, call, result)
            return result
        if getattr(call, "duplicate_of", None):
            result = ToolResult.fail(
                f"Not run again: this exact {call.name} call already ran in round {call.duplicate_of} of this task "
                "and nothing has changed since, so its result above is still current. Use that result; if it was "
                "not what you needed, try a different approach (other arguments, another tool) or answer the user.")
            result.summary = tr("Повтор того же вызова — не выполнен, результат выше", "Same call repeated — not run, see the result above")
            call.status = "error"
            self.ui.tool_started(self, call)
            self.ui.tool_finished(self, call, result)
            return result
        if "_invalid_json" in call.args:
            # A native call whose arguments did not arrive as readable JSON (cut off by the output limit, or
            # malformed): running it with no arguments would give a misleading "missing argument" error.
            raw = str(call.args.get("_invalid_json") or "")
            result = ToolResult.fail(
                f"The arguments of your {call.raw_name or call.name} call were not valid JSON or were cut off "
                f"(the call was NOT executed). Received: {raw[:300]!r}{'…' if len(raw) > 300 else ''}. Send the "
                "call again with complete JSON arguments; for a long file write it in smaller parts (Write a "
                "first part, then Edit to append) so one call stays well under the output limit.")
            result.summary = tr("Аргументы вызова не прочитаны — модель пришлёт его заново",
                                "Call arguments could not be read — the model will resend it")
            call.status = "error"
            self.ui.tool_started(self, call)
            self.ui.tool_finished(self, call, result)
            return result
        ctx = ToolContext(runtime=runtime, call=call, turn=turn, depth=self.depth,
                          progress=lambda text: self.ui.tool_progress(self, call, text),
                          agent_session=self.session if not self.is_main else None, ui=self.ui)
        self.ui.tool_started(self, call)
        try:
            tool.check_required(call.args)
            prepared: Prepared = await tool.prepare(ctx, call.args)
            outcome = await runtime.hooks.run("PreToolUse", {"tool_name": tool.name, "tool_input": call.args},
                                              match=tool.name)
            for message in outcome.messages:
                self.ui.notice(message, "warn")
            if outcome.decision == "deny":
                raise ToolError(tr("Заблокировано хуком: ", "Blocked by a hook: ") + (outcome.reason or ""))
            if outcome.decision != "allow":
                decision = await runtime.permissions.check(call, tool, prepared)
                feedback, stop = decision.feedback, decision.stop
                if not decision.allow:
                    call.status = "denied"
                    if stop:
                        self.stop_after_round = True
                    if feedback:
                        self.feedback.append(feedback)
                    reason = decision.reason or tr("отклонено", "denied")
                    text = ("The user denied this action." if decision.source == "user" else reason)
                    if stop and not feedback:
                        text += " The user wants to tell you what to do instead — stop and wait for their message."
                    result = ToolResult(ok=False, error=text, summary=reason, meta={"denied": True})
                    self.ui.tool_finished(self, call, result)
                    return result
            call.status = "running"
            result = await tool.run(ctx, call.args, prepared)
            if outcome.context:
                result.output = (result.output if isinstance(result.output, str)
                                 else json.dumps(result.output, ensure_ascii=False)) + "\n" + "\n".join(outcome.context)
            post = await runtime.hooks.run("PostToolUse", {"tool_name": tool.name, "tool_input": call.args,
                                                           "tool_response": {"ok": result.ok,
                                                                             "summary": result.summary}},
                                           match=tool.name)
            for message in post.messages:
                self.ui.notice(message, "warn")
            if post.decision == "block" and post.reason:
                result.output = (result.output if isinstance(result.output, str) else json.dumps(result.output)) + \
                    f"\n[PostToolUse hook feedback] {post.reason}"
            if post.context:
                result.output = (result.output if isinstance(result.output, str) else json.dumps(result.output)) + \
                    "\n" + "\n".join(post.context)
            if call.notes:
                result.output = (result.output if isinstance(result.output, str)
                                 else json.dumps(result.output, ensure_ascii=False)) + "\n" + _repair_note(call.notes)
            call.status = "done" if result.ok else "error"
        except ToolError as exc:
            result = ToolResult.fail(str(exc))
            call.status = "error"
        except asyncio.CancelledError:
            call.status = "cancelled"
            raise
        except Exception as exc:
            runtime.log_error(traceback.format_exc())
            result = ToolResult.fail(f"{type(exc).__name__}: {exc}")
            call.status = "error"
        call.finished = time.monotonic()
        call.result = result
        runtime.stats.tool_calls += 1
        if not result.ok:
            runtime.stats.tool_errors += 1
        if result.diff:
            runtime.stats.count_diff(result.diff)
        elif result.ok and result.meta.get("lines_added"):
            runtime.stats.lines_added += int(result.meta["lines_added"])   # a new file has no diff
        self.ui.tool_finished(self, call, result)
        return result


_OVERFLOW = re.compile(
    r"context[_ ]length[_ ]exceeded|maximum context length|context window|prompt is too long|input is too long|"
    r"too many (?:input )?tokens|exceeds? the (?:model'?s? )?(?:maximum )?(?:context|token limit)|request too large|"
    r"reduce the length of the messages|token limit exceeded|max(?:imum)?[_ ]input[_ ]tokens|"
    r"превыш\w* (?:длин\w* )?контекст|слишком длинн\w* (?:запрос|контекст)", re.I)


def context_overflow(exc: BaseException) -> bool:
    """Would a smaller history cure this provider error? (context window, the Proxy's message cap, body size)

    Round 32: classified by app/cli/errors.py — the upstream's words first; «No available accounts» or a
    rate limit is never taken for an overflow, whatever the status."""
    from .errors import is_overflow
    return is_overflow(exc)


def _retryable(exc: BaseException) -> bool:
    """Round 32: a temporary upstream state by its own words (no free account, rate limit, timeout) is worth a
    short retry even when the transport did not mark it so."""
    from .errors import info_of
    return info_of(exc).retryable


def _slim_args(args: Dict[str, Any], limit: int = 600) -> Dict[str, Any]:
    """Arguments for the record of a result: a whole file written by a call is already in the call itself."""
    slim: Dict[str, Any] = {}
    for key, value in (args or {}).items():
        if isinstance(value, str) and len(value) > limit:
            slim[key] = value[:limit] + f"… [{len(value) - limit} more characters]"
        else:
            slim[key] = value
    return slim


CONTENT_EXAMPLE = (
    "```memuai-tool\n"
    "[{\"tool\": \"Write\", \"args\": {\"file_path\": \"src/App.jsx\", \"content\": \"@content:1\"}}]\n"
    "```\n"
    "<memuai-content id=\"1\">\n"
    "the whole file, exactly as it must be written\n"
    "</memuai-content>")


def _invalid_text(info: Dict[str, Any]) -> str:
    """What the model is told when one of its tool calls could not be run.

    The message says what was wrong *and* how to say it right, and gets blunter the more often the same mistake
    repeats: a model that is told "invalid JSON" three times tends to send the same JSON a fourth time."""
    try:
        streak = max(1, int(info.get("streak") or 1))
    except (TypeError, ValueError):
        streak = 1
    if info.get("kind") == "cut_off":
        tool = info.get("tool") or "a tool"
        why = ("The answer hit the output length limit while this call was being written."
               if info.get("limit") else "The answer ended before the JSON of this call was complete.")
        text = (f"Your {tool} call was cut off. {why} It was NOT executed. Send that one call again. For a big file "
                "use a raw content block (see \"Big files\" in the tool protocol) or split the file into several "
                "smaller Write/Edit calls — keep one message under about 200 lines of file content.")
    else:
        error = str(info.get("error") or "")[:700]
        text = "One tool call in your memuai-tool block could not be read: " + error + ". "
        if "content block" in error and ("missing" in error or "cut off" in error):
            text += ("The call refers to a raw content block that did not arrive with it. NOTHING was changed. Send "
                     "the call again in one of two ways: (a) put the text inline in the JSON argument, escaped "
                     "(\\n for a line break, \\\" for a quote) — best for anything under ~30 lines; or (b) write the "
                     "tool block and, in the SAME message right after its closing ```, one block per reference, "
                     "numbered exactly as referenced:\n" + CONTENT_EXAMPLE + "\n"
                     "For several changes to one file use one Edit call per change instead of MultiEdit with "
                     "references. ")
        elif "expected" in error or "unexpected" in error or "line " in error:
            text += ("The brackets or quotes of that call do not balance. Write it again slowly: every { has a }, "
                     "every [ has a ], a double quote inside a string is \\\", a backslash is \\\\, a line break is "
                     "\\n — or move the long text into a raw content block (\"@content:1\" + a "
                     "<memuai-content id=\"1\"> block after the tool block). ")
        elif "holds no tool call" in error or "not a tool call" in error:
            text += ("Every entry of the array must look like {\"tool\": \"Read\", \"args\": {\"file_path\": "
                     "\"a.py\"}}. ")
        text += ("The other calls in that block were run. Send only the failed call again as valid JSON — inside "
                 "strings write a double quote as \\\" , a backslash as \\\\ and a line break as \\n — or put the "
                 "text into a raw content block.")
    if streak >= 2:
        text += (f"\nThis is the {streak}{'nd' if streak == 2 else 'rd' if streak == 3 else 'th'} unreadable call in a "
                 "row. Do not send the same thing again. Simplify: ONE call per message, short arguments, and "
                 "inline strings (no raw content blocks) unless the text is long. If an Edit keeps failing, "
                 "Read the file again and use a shorter old_string — one distinctive line.")
    return text


def _repair_note(notes: List[str]) -> str:
    return ("[note] The JSON of this tool call was not strictly valid (" + ", ".join(notes[:4])
            + ") and was repaired automatically; the call ran. Next time escape quotes and backslashes, or use a "
              "raw content block, so a stricter reader does not reject it.")


def _plain(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return str(content or "")


def _with_context(content: Any, context: List[str]) -> Any:
    block = "\n\n<hook_context>\n" + "\n".join(context) + "\n</hook_context>"
    if isinstance(content, str):
        return content + block
    if isinstance(content, list):
        return [*content, {"type": "text", "text": block}]
    return content
