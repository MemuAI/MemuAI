"""``/auto-continue`` — send "continue" by itself later, when the model's limits are back.

Round 33. The answer stopped (or the request failed) because the provider's limits ran out; the user wants the work
to go on as soon as the key works again without sitting at the terminal. ``/auto-continue 18:30`` (``+2h``,
``in 45m``, ``tomorrow 09:00``, ``auto`` …) arms one send of "continue" (or the user's own text) into *this*
conversation:

* ``auto`` takes the reset time the provider named (``Retry-After`` and friends, or the error text — see
  ``ratelimit.py``); when nobody named one it starts with :data:`AUTO_FALLBACK` and backs off;
* a countdown sits in the status line; ``/auto-continue status`` / ``cancel`` / ``now``;
* if the request is limited again, it re-arms itself (the reset time, else a doubling pause) up to ``retries`` times,
  and — when a fallback model is given — switches to it for the next try;
* the plan is kept in the conversation (``session.meta["auto_continue"]``): closing MemuCode stops the countdown (the
  terminal must stay open — nothing is installed in the system), and opening the conversation again (``/resume``,
  ``memucode -c``) re-arms it; a time that passed while it was closed is offered with a short grace period;
* a limit error offers the command right in the error message.

Pure functions (time parsing, arguments, backoff) are at the top and tested without a terminal; :class:`AutoContinue`
is the part bound to the interface (``TerminalUI.auto_continue``), driven by its ticker and ``turn_finished``.
"""
from __future__ import annotations

import re
import shlex
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .i18n import tr

#: Error categories (``errors.classify``) that mean "the limits are out, it will work later".
LIMIT_CATEGORIES = ("rate_limit", "quota", "upstream_no_accounts", "proxy_no_accounts")
#: …and the ones worth another try later as well (the service is down for now).
RETRY_CATEGORIES = LIMIT_CATEGORIES + ("server", "timeout", "network", "proxy_offline", "model_unavailable")
DEFAULT_RETRIES = 4
MAX_RETRIES = 20
AUTO_FALLBACK = 15 * 60          # "auto" when the provider did not say when the limit ends
BACKOFF_BASE = 5 * 60            # a retry with no reset time: 5, 10, 20, 40, 60, 60 … minutes
BACKOFF_CAP = 60 * 60
MIN_DELAY = 20                   # never fire sooner than this after arming from a reset time
RESET_MARGIN = 5                 # seconds after the named reset, so the first request is not a hair too early
GRACE = 60                       # a time that passed while MemuCode was closed fires this long after reopening
MAX_AHEAD = 7 * 86400
SUBCOMMANDS = ("status", "cancel", "now", "auto")
CANCEL_WORDS = ("cancel", "stop", "off", "clear", "отмена", "отменить", "стоп", "выкл")
STATUS_WORDS = ("status", "info", "статус")
NOW_WORDS = ("now", "сейчас")
AUTO_WORDS = ("auto", "reset", "авто", "сброс")
#: The menu of argument suggestions (tui/input.py CHOICES).
CHOICES = ("auto", "status", "cancel", "now", "+15m", "+30m", "+1h", "+2h", "+5h", "tomorrow 09:00")
DEFAULT_MESSAGE = "continue"


class TimeError(ValueError):
    pass


# ---------------------------------------------------------------- time

_UNITS = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "с": 1, "сек": 1, "секунд": 1, "секунду": 1,
          "секунды": 1,
          "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "м": 60, "мин": 60, "минут": 60, "минуту": 60,
          "минуты": 60,
          "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600, "ч": 3600, "час": 3600, "часа": 3600,
          "часов": 3600,
          "d": 86400, "day": 86400, "days": 86400, "д": 86400, "день": 86400, "дня": 86400, "дней": 86400}
_UNIT_RE = "|".join(sorted((re.escape(unit) for unit in _UNITS), key=len, reverse=True))
_DURATION = re.compile(rf"^(?:(\d+(?:[.,]\d+)?)\s*({_UNIT_RE}))+$", re.I)
_DURATION_PIECE = re.compile(rf"(\d+(?:[.,]\d+)?)\s*({_UNIT_RE})(?![a-zа-я])", re.I)
_CLOCK = re.compile(r"^(\d{1,2})(?:[:.h](\d{2}))?\s*(am|pm)?$", re.I)
_DAY_WORDS = {"today": 0, "сегодня": 0, "tomorrow": 1, "завтра": 1, "tmrw": 1, "послезавтра": 2}
_IN_WORDS = ("in", "через", "after", "+")
_AT_WORDS = ("at", "в", "on", "@")


def _duration(text: str) -> float:
    text = text.strip().lower().replace(" ", "")
    if not text or not _DURATION.match(text):
        return 0.0
    return sum(float(number.replace(",", ".")) * _UNITS[unit.lower()] for number, unit in _DURATION_PIECE.findall(text))


def _clock(text: str) -> Optional[Tuple[int, int]]:
    match = _CLOCK.match(text.strip().lower())
    if not match:
        return None
    hour, minute, half = int(match.group(1)), int(match.group(2) or 0), match.group(3)
    if match.group(2) is None and not half:
        return None                                   # "18" alone is not a time (it may be the start of a message)
    if half:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if half == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def parse_time(text: str, now: Optional[float] = None) -> float:
    """``"18:30"``, ``"6:30pm"``, ``"+2h"``, ``"in 45m"``, ``"через 2 часа"``, ``"1h30m"``, ``"tomorrow 09:00"``,
    ``"завтра 9:00"``, ``"2026-10-12 09:00"`` → an absolute time (``time.time()`` seconds).

    A clock time that already passed today means tomorrow. Raises :class:`TimeError` with examples."""
    now = time.time() if now is None else float(now)
    raw = " ".join(str(text or "").strip().lower().split())
    if not raw:
        raise TimeError(tr("Не сказано, когда.", "No time was given."))
    words = raw.split(" ")
    if words[0] in _IN_WORDS or raw.startswith("+"):
        rest = raw[1:] if raw.startswith("+") else " ".join(words[1:])
        seconds = _duration(rest)
        if seconds <= 0:
            raise TimeError(_examples(text))
        return _bounded(now + seconds, now, text)
    seconds = _duration(raw)
    if seconds > 0:
        return _bounded(now + seconds, now, text)
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})[ t](\d{1,2}[:.]\d{2}(?:\s*[ap]m)?)", raw)
    if iso:
        clock = _clock(iso.group(4))
        if clock is None:
            raise TimeError(_examples(text))
        try:
            moment = datetime(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)), clock[0], clock[1])
        except ValueError as exc:
            raise TimeError(_examples(text)) from exc
        if moment.timestamp() <= now:
            raise TimeError(tr(f"{text} — это уже прошло.", f"{text} is already in the past."))
        return _bounded(moment.timestamp(), now, text)
    days = None
    if words[0] in _AT_WORDS:
        words = words[1:]
    if words and words[0] in _DAY_WORDS:
        days = _DAY_WORDS[words[0]]
        words = words[1:]
        if words and words[0] in _AT_WORDS:
            words = words[1:]
    clock = _clock(" ".join(words)) if words else ((9, 0) if days else None)
    if clock is None:
        raise TimeError(_examples(text))
    base = datetime.fromtimestamp(now).replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)
    if days is not None:
        moment = base + timedelta(days=days)
        if moment.timestamp() <= now:
            raise TimeError(tr(f"{text} — это уже прошло.", f"{text} is already in the past."))
    else:
        moment = base if base.timestamp() > now else base + timedelta(days=1)
    return _bounded(moment.timestamp(), now, text)


def _bounded(at: float, now: float, text: str) -> float:
    if at - now > MAX_AHEAD:
        raise TimeError(tr(f"«{text}» — дальше чем через неделю; так далеко нельзя.",
                           f"“{text}” is more than a week away — that is too far."))
    return at


def _examples(text: str) -> str:
    return tr(f"Не понимаю время «{text}». Примеры: 18:30 · +2h · in 45m · через 2 часа · tomorrow 09:00 · auto",
              f"I do not understand the time “{text}”. Examples: 18:30 · +2h · in 45m · 1h30m · tomorrow 09:00 · auto")


# ---------------------------------------------------------------- arguments

@dataclass
class Request:
    """``/auto-continue`` arguments after parsing."""
    action: str = "arm"              # arm | status | cancel | now | pick (no time given) | help
    at: float = 0.0                  # absolute time for "arm" with a time
    auto: bool = False               # "auto": the provider's reset time
    message: str = ""
    retries: Optional[int] = None
    fallback: Optional[str] = None
    error: str = ""


def parse_args(args: str, now: Optional[float] = None) -> Request:
    """``[time|duration|auto] [message] [--retries N] [--no-retry] [--fallback MODEL]`` and the sub-commands."""
    now = time.time() if now is None else now
    try:
        words = shlex.split(args or "", posix=True)
    except ValueError:
        words = (args or "").split()
    request = Request()
    rest: List[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        low = word.lower()
        if low in ("--retries", "--retry", "-n") and index + 1 < len(words):
            try:
                request.retries = max(0, min(MAX_RETRIES, int(words[index + 1])))
            except ValueError:
                request.error = tr("--retries ждёт число", "--retries needs a number")
                return request
            index += 2
            continue
        if low.startswith("--retries="):
            try:
                request.retries = max(0, min(MAX_RETRIES, int(low.split("=", 1)[1])))
            except ValueError:
                request.error = tr("--retries ждёт число", "--retries needs a number")
                return request
            index += 1
            continue
        if low == "--no-retry":
            request.retries = 0
            index += 1
            continue
        if low in ("--fallback", "--model", "-f") and index + 1 < len(words):
            request.fallback = words[index + 1].strip()
            index += 2
            continue
        if low.startswith(("--fallback=", "--model=")):
            request.fallback = word.split("=", 1)[1].strip()
            index += 1
            continue
        if low in ("--help", "-h", "help", "?"):
            request.action = "help"
            return request
        rest.append(word)
        index += 1
    if not rest:
        request.action = "pick"
        return request
    head = rest[0].lower()
    if len(rest) == 1 and head in CANCEL_WORDS:
        request.action = "cancel"
        return request
    if len(rest) == 1 and head in STATUS_WORDS:
        request.action = "status"
        return request
    if head in NOW_WORDS:
        request.action = "now"
        request.message = " ".join(rest[1:]).strip()
        return request
    if head in AUTO_WORDS:
        request.auto = True
        request.message = " ".join(rest[1:]).strip()
        return request
    # The longest leading run of words that reads as a time is the time; the rest is the message.
    for size in range(min(4, len(rest)), 0, -1):
        try:
            request.at = parse_time(" ".join(rest[:size]), now)
        except TimeError:
            continue
        request.message = " ".join(rest[size:]).strip()
        return request
    try:
        parse_time(rest[0], now)
    except TimeError as exc:
        request.error = str(exc)
    return request


# ---------------------------------------------------------------- timing

def backoff(attempt: int) -> float:
    """The pause before retry number ``attempt`` (1, 2, …) when the provider named no reset time."""
    return float(min(BACKOFF_CAP, BACKOFF_BASE * (2 ** max(0, attempt - 1))))


def next_try(attempt: int, reset: float, now: Optional[float] = None) -> Tuple[float, str]:
    """When to try again after a failed try number ``attempt``: the named reset (``"reset"``) or a backoff."""
    now = time.time() if now is None else now
    if reset and reset > now:
        return max(reset + RESET_MARGIN, now + MIN_DELAY), "reset"
    return now + backoff(attempt), "backoff"


def countdown(seconds: float) -> str:
    """``"0:42"``, ``"14:05"``, ``"2:03:07"``, ``"1d 3h"``."""
    seconds = max(0, int(round(seconds)))
    if seconds >= 86400:
        return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def clock_text(at: float, now: Optional[float] = None) -> str:
    """``"18:30"`` today, ``"tomorrow 09:00"``, ``"Mon 09:00"`` later in the week."""
    now = time.time() if now is None else now
    moment, today = datetime.fromtimestamp(at), datetime.fromtimestamp(now)
    days = (moment.date() - today.date()).days
    hhmm = moment.strftime("%H:%M")
    if days <= 0:
        return hhmm
    if days == 1:
        return tr(f"завтра {hhmm}", f"tomorrow {hhmm}")
    return moment.strftime("%a ") + hhmm


def default_message(settings: Any = None) -> str:
    custom = ""
    if settings is not None:
        try:
            custom = str(settings.get("auto_continue.message") or "").strip()
        except Exception:
            custom = ""
    return custom or DEFAULT_MESSAGE


def setting_retries(settings: Any) -> int:
    try:
        value = settings.get("auto_continue.retries")
        return DEFAULT_RETRIES if value is None else max(0, min(MAX_RETRIES, int(value)))
    except Exception:
        return DEFAULT_RETRIES


def setting_fallback(settings: Any) -> str:
    try:
        return str(settings.get("auto_continue.fallback_model") or "").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------- the plan

@dataclass
class Plan:
    at: float
    message: str = DEFAULT_MESSAGE
    custom_message: bool = False
    source: str = "manual"           # manual | auto (the provider's reset time) | limit (auto with no reset known)
    retries: int = DEFAULT_RETRIES
    attempt: int = 0                 # tries made so far
    fallback: str = ""
    state: str = "armed"             # armed | sent
    created: float = field(default_factory=time.time)
    session: str = ""
    model: str = ""                  # the model that was limited
    last_error: str = ""

    @classmethod
    def from_dict(cls, data: Any) -> Optional["Plan"]:
        if not isinstance(data, dict):
            return None
        try:
            known = {key: data[key] for key in cls.__dataclass_fields__ if key in data}
            plan = cls(**known)
            plan.at = float(plan.at)
            plan.attempt, plan.retries = int(plan.attempt), int(plan.retries)
            return plan if plan.at > 0 else None
        except (TypeError, ValueError):
            return None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AutoContinue:
    """The armed ``/auto-continue`` of one terminal. ``tick()`` is called by the interface's ticker; it fires the plan
    when it is due and the terminal is free, and notices a switch of conversation (re-arming a stored plan)."""

    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.plan: Optional[Plan] = None
        self.awaiting = False                    # our "continue" is the turn in progress
        self.last_limit: Dict[str, Any] = {}     # the newest limit error: {"at": reset or 0, "category", "model", "when"}
        self._session_id: Optional[str] = None
        self._last_second = -1

    # ------------------------------------------------------------ helpers
    @property
    def runtime(self) -> Any:
        return self.ui.runtime

    def _say(self, text: str, level: str = "info") -> None:
        try:
            self.ui.say(text, level)
        except Exception:
            pass

    def _persist(self) -> None:
        session = self.runtime.session
        try:
            session.set_meta(auto_continue=self.plan.to_dict() if self.plan is not None else None)
        except Exception:
            pass

    def armed(self) -> bool:
        return self.plan is not None

    def remaining(self, now: Optional[float] = None) -> float:
        if self.plan is None:
            return 0.0
        return max(0.0, self.plan.at - (time.time() if now is None else now))

    # ------------------------------------------------------------ arming
    def arm(self, at: float, *, message: str = "", source: str = "manual", retries: Optional[int] = None,
            fallback: Optional[str] = None) -> Plan:
        settings = self.runtime.settings
        replaced = self.plan is not None
        self.plan = Plan(at=float(at), message=message.strip() or default_message(settings),
                         custom_message=bool(message.strip()), source=source,
                         retries=setting_retries(settings) if retries is None else int(retries),
                         fallback=setting_fallback(settings) if fallback is None else fallback.strip(),
                         session=self.runtime.session.id, model=str(self.runtime.model or ""),
                         last_error=str(self.last_limit.get("text") or "")[:300])
        self.awaiting = False
        self._session_id = self.runtime.session.id
        self._persist()
        self._last_second = -1
        if replaced:
            self._say(tr("Прежнее автопродолжение заменено.", "The previous auto-continue was replaced."))
        return self.plan

    def arm_auto(self, *, message: str = "", retries: Optional[int] = None, fallback: Optional[str] = None,
                 now: Optional[float] = None) -> Tuple[Plan, bool]:
        """Arm at the reset time the provider named; False in the pair when none is known (a fallback pause is used)."""
        now = time.time() if now is None else now
        reset = float(self.last_limit.get("at") or 0.0)
        if reset > now:
            return self.arm(max(reset + RESET_MARGIN, now + MIN_DELAY), message=message, source="auto",
                            retries=retries, fallback=fallback), True
        return self.arm(now + AUTO_FALLBACK, message=message, source="limit", retries=retries, fallback=fallback), False

    def cancel(self, *, quiet: bool = False) -> bool:
        had = self.plan is not None
        self.plan = None
        self.awaiting = False
        if had or self.runtime.session.meta.get("auto_continue"):
            self._persist()
        if had and not quiet:
            self._say(tr("Автопродолжение отменено.", "Auto-continue cancelled."), "success")
        return had

    # ------------------------------------------------------------ texts
    def describe(self, now: Optional[float] = None) -> str:
        now = time.time() if now is None else now
        plan = self.plan
        if plan is None:
            return tr("Автопродолжение не назначено.", "No auto-continue is set.")
        when = clock_text(plan.at, now)
        bits = [tr(f"Отправлю «{plan.message[:60]}» в {when} (через {countdown(plan.at - now)})",
                   f"Sends “{plan.message[:60]}” at {when} (in {countdown(plan.at - now)})")]
        if plan.source == "auto":
            bits.append(tr("время сброса лимита от провайдера", "the provider's limit reset time"))
        elif plan.source == "limit":
            bits.append(tr("провайдер не назвал время сброса", "the provider did not name a reset time"))
        tries = tr(f"попытка {plan.attempt + 1} из {plan.retries + 1}", f"try {plan.attempt + 1} of {plan.retries + 1}")
        bits.append(tries)
        if plan.fallback:
            bits.append(tr(f"запасная модель: {plan.fallback}", f"fallback model: {plan.fallback}"))
        return " · ".join(bits)

    def footer(self, now: Optional[float] = None) -> str:
        """The status-line text: ``◷ continue in 14:05``; empty when nothing is armed."""
        plan = self.plan
        if plan is None:
            return ""
        now = time.time() if now is None else now
        if self.awaiting or plan.state == "sent":
            return tr("◷ автопродолжение: отправлено", "◷ auto-continue: sent")
        left = plan.at - now
        if left <= 0:
            return tr("◷ продолжу, когда освободится", "◷ continue when idle")
        retry = f" · {tr('повтор', 'retry')} {plan.attempt}/{plan.retries}" if plan.attempt else ""
        return tr(f"◷ продолжу через {countdown(left)}{retry}", f"◷ continue in {countdown(left)}{retry}")

    def offer(self, now: Optional[float] = None) -> str:
        """The line under a limit error: how to continue by itself later."""
        now = time.time() if now is None else now
        if self.plan is not None:
            return tr(f"◷ Автопродолжение уже назначено: {clock_text(self.plan.at, now)} · /auto-continue status",
                      f"◷ Auto-continue is already set for {clock_text(self.plan.at, now)} · /auto-continue status")
        reset = float(self.last_limit.get("at") or 0.0)
        if reset > now:
            at = max(reset + RESET_MARGIN, now + MIN_DELAY)
            return tr(f"◷ Лимит сбросится в {clock_text(at, now)} (через {countdown(at - now)}). "
                      "/auto-continue auto — отправить «continue» само в это время",
                      f"◷ The limit resets at {clock_text(at, now)} (in {countdown(at - now)}). "
                      "/auto-continue auto sends “continue” by itself then")
        return tr("◷ /auto-continue 30m (или 18:30, +2h, auto) — отправить «continue» само позже",
                  "◷ /auto-continue 30m (or 18:30, +2h, auto) sends “continue” by itself later")

    # ------------------------------------------------------------ errors
    def note_error(self, result: Any, now: Optional[float] = None) -> str:
        """Remember a limit error (its reset time) and return the offer for the transcript ("" for other errors)."""
        now = time.time() if now is None else now
        category = str(getattr(result, "error_category", "") or "")
        if category not in LIMIT_CATEGORIES:
            return ""
        from . import ratelimit
        reset = float(getattr(result, "retry_at", 0.0) or 0.0)
        if not ratelimit._sane(reset, now):
            reset = ratelimit.from_text(str(getattr(result, "error", "") or ""), now)
        self.last_limit = {"at": reset, "category": category, "model": str(self.runtime.model or ""), "when": now,
                           "text": str(getattr(result, "error", "") or "").splitlines()[0][:200]
                           if getattr(result, "error", "") else ""}
        return self.offer(now)

    # ------------------------------------------------------------ the loop
    def tick(self, now: Optional[float] = None) -> bool:
        """Called often by the interface. True when the status line should be redrawn."""
        now = time.time() if now is None else now
        changed = False
        session = self.runtime.session
        if session.id != self._session_id:
            changed = self._session_switched(now) or changed
        plan = self.plan
        if plan is None:
            return changed
        second = int(now)
        if second != self._last_second:
            self._last_second = second
            changed = True
        if plan.state == "armed" and not self.awaiting and now >= plan.at and self._free():
            self.fire(now)
            changed = True
        return changed

    def _free(self) -> bool:
        ui = self.ui
        return not (getattr(ui, "busy", False) or getattr(ui, "dialogs", None) or getattr(ui, "connecting", False))

    def _session_switched(self, now: float) -> bool:
        previous = self._session_id
        self._session_id = self.runtime.session.id
        if self.plan is not None and previous is not None and self.plan.session == previous:
            # The plan stays stored with its own conversation and comes back with it.
            self._say(tr(f"Автопродолжение прошлого разговора на паузе — вернётся вместе с ним (/resume {previous}).",
                         f"The auto-continue of the previous conversation is paused — it comes back with it "
                         f"(/resume {previous})."), "warn")
            self.plan = None
            self.awaiting = False
        stored = Plan.from_dict(self.runtime.session.meta.get("auto_continue"))
        if stored is None:
            return previous is not None
        stored.session = self.runtime.session.id
        stored.state = "armed"
        self.plan = stored
        self.awaiting = False
        if stored.at <= now:
            stored.at = now + GRACE
            self._persist()
            self._say(tr(f"Автопродолжение этого разговора не успело сработать, пока MemuCode был закрыт. Отправлю "
                         f"«{stored.message[:60]}» через {GRACE} с · /auto-continue cancel — отменить.",
                         f"This conversation's auto-continue was due while MemuCode was closed. Sending "
                         f"“{stored.message[:60]}” in {GRACE}s · /auto-continue cancel to stop."), "warn")
        else:
            self._say(tr(f"◷ Автопродолжение этого разговора снова взведено: {clock_text(stored.at, now)} "
                         "(окно должно оставаться открытым).",
                         f"◷ This conversation's auto-continue is armed again for {clock_text(stored.at, now)} "
                         "(keep this window open)."))
        return True

    def fire(self, now: Optional[float] = None) -> None:
        """Send the plan's message now (the terminal is free)."""
        plan = self.plan
        if plan is None:
            return
        runtime = self.runtime
        if plan.attempt > 0 and plan.fallback and plan.fallback != runtime.model:
            previous = runtime.model
            try:
                runtime.set_model(plan.fallback)
                self._say(tr(f"◷ Лимит не отпускает — переключаюсь на запасную модель {plan.fallback} "
                             f"(была {previous}; вернуть: /model {previous}).",
                             f"◷ Still limited — switching to the fallback model {plan.fallback} "
                             f"(was {previous}; switch back with /model {previous})."), "warn")
            except Exception as exc:
                self._say(tr(f"Не удалось переключиться на {plan.fallback}: {exc}",
                             f"Could not switch to {plan.fallback}: {exc}"), "warn")
        plan.attempt += 1
        plan.state = "sent"
        self.awaiting = True
        self._persist()
        tries = "" if plan.attempt == 1 else tr(f" (попытка {plan.attempt} из {plan.retries + 1})",
                                                f" (try {plan.attempt} of {plan.retries + 1})")
        self._say(tr(f"◷ Автопродолжение: отправляю «{plan.message[:80]}»{tries}",
                     f"◷ Auto-continue: sending “{plan.message[:80]}”{tries}"), "info")
        try:
            self.ui.send_prompt(plan.message, display=plan.message)
        except Exception as exc:
            self.awaiting = False
            plan.state = "armed"
            plan.at = time.time() + backoff(plan.attempt)
            self._persist()
            self._say(tr(f"Не удалось отправить: {exc}", f"Could not send: {exc}"), "error")

    def turn_finished(self, result: Any, now: Optional[float] = None) -> None:
        """The end of a main turn: settle our try, or remember a limit error and offer the command."""
        now = time.time() if now is None else now
        stopped = str(getattr(result, "stopped", "") or "")
        if not self.awaiting:
            if stopped == "error":
                line = self.note_error(result, now)
                if line:
                    self._say(line, "info")
            return
        self.awaiting = False
        plan = self.plan
        if plan is None:
            return
        if stopped == "interrupted":
            self.cancel(quiet=True)
            self._say(tr("Автопродолжение прервано (Esc) и отменено.", "Auto-continue was interrupted (Esc) and cancelled."),
                      "warn")
            return
        if stopped != "error":
            self.cancel(quiet=True)
            self._say(tr("◷ Автопродолжение сработало — модель снова отвечает.",
                         "◷ Auto-continue worked — the model is answering again."), "success")
            try:
                self.ui._notify("MemuCode", tr("Автопродолжение сработало", "Auto-continue worked"))
            except Exception:
                pass
            return
        category = str(getattr(result, "error_category", "") or "")
        if category not in RETRY_CATEGORIES:
            self.cancel(quiet=True)
            self._say(tr("Автопродолжение остановлено: эта ошибка не пройдёт сама со временем.",
                         "Auto-continue stopped: this error will not go away by waiting."), "warn")
            return
        if plan.attempt > plan.retries:
            self.cancel(quiet=True)
            self._say(tr(f"Автопродолжение сдалось после {plan.attempt} попыток. Назначьте заново: /auto-continue.",
                         f"Auto-continue gave up after {plan.attempt} tries. Set it again with /auto-continue."), "warn")
            try:
                self.ui._notify("MemuCode", tr("Автопродолжение не удалось", "Auto-continue did not get through"))
            except Exception:
                pass
            return
        self.note_error(result, now)
        reset = float(self.last_limit.get("at") or 0.0) if category in LIMIT_CATEGORIES else 0.0
        at, why = next_try(plan.attempt, reset, now)
        plan.at, plan.state = at, "armed"
        plan.last_error = str(getattr(result, "error", "") or "").splitlines()[0][:300] if getattr(result, "error", "") else ""
        self._persist()
        reason = tr("время сброса от провайдера", "the provider's reset time") if why == "reset" else \
            tr(f"пауза {int(backoff(plan.attempt) // 60)} мин", f"a {int(backoff(plan.attempt) // 60)} min pause")
        switch = tr(f"; затем — запасная модель {plan.fallback}", f"; the next try uses the fallback model {plan.fallback}") \
            if plan.fallback and plan.fallback != self.runtime.model else ""
        self._say(tr(f"◷ Всё ещё лимит. Следующая попытка в {clock_text(at, now)} ({reason}), "
                     f"повтор {plan.attempt} из {plan.retries}{switch}.",
                     f"◷ Still limited. Next try at {clock_text(at, now)} ({reason}), "
                     f"retry {plan.attempt} of {plan.retries}{switch}."), "warn")


HELP = (
    "/auto-continue [time|duration|auto] [message] [--retries N | --no-retry] [--fallback MODEL]\n"
    "  Sends “continue” (or your message) into this conversation by itself later — for when the model's limits\n"
    "  ran out. Times: 18:30 · 6:30pm · +2h · in 45m · 1h30m · tomorrow 09:00 · 2026-10-12 09:00.\n"
    "  auto       the reset time the provider named in its limit error (else 15 min, then backing off)\n"
    "  status     what is set and when · cancel  drop it · now  send it right away\n"
    "  If the request is limited again it retries (the reset time, else 5, 10, 20, 40, 60 min) — --retries N,\n"
    "  default 4 (/config set auto_continue.retries N); --fallback MODEL switches to that model for the retries\n"
    "  (/config set auto_continue.fallback_model MODEL). The countdown is in the status line.\n"
    "  Keep this window open: nothing is installed in the system. The plan is saved with the conversation and\n"
    "  re-armed when you open it again (/resume, memucode -c). Esc during the auto-sent request cancels it.")
