"""Round 29 — the stuck detector shared by every agent loop.

An agent loop (Do For You, Work Browser, Computer Use, MemuCode CLI; Work in the
window uses the JS mirror ``pgstatic/stuck.js``) reports each step *before* it
is executed::

    detector = StuckDetector(sensitivity)
    verdict = detector.observe({"action": "click", "element": 4}, state=page_hash,
                               label="click element 4")
    if verdict.level == "pause":
        decision = await ask_user(verdict.to_card())   # continue | hint | replan | stop
        detector.acknowledge(decision["decision"])
    ...perform the action...
    detector.note_result(error=None)

Because the check runs before the action, a pause never leaves an action half
applied: the repeating action is simply not performed, and after "Continue" the
loop takes a fresh look and asks the model again.

Signals (each one has a threshold per sensitivity, see ``PROFILES``):

* ``repeat``       — the same normalized action (+args) again on an unchanged state,
                     or many times on states that were already seen;
* ``oscillation``  — A-B-A-B (or A-B-C-A-B-C) with the states cycling too;
* ``same_error``   — the same (normalized) error N times in the window;
* ``no_new_state`` — N steps in a row bring only pages/screens already seen;
* ``waiting``      — a polling ``wait`` that goes on long after the state froze;
* ``plan_stalled`` — the plan step / progress marker does not move for N iterations
                     and no files change;
* ``no_evidence``  — the model keeps claiming progress, nothing observable changes;
* ``budget``       — many steps with no new evidence of any kind;
* ``model_signal`` — the model itself said it is stuck (protocol action ``stuck``).

False positives are avoided on purpose: ``wait``/polling steps are passive (only
the long ``waiting`` rule sees them), scrolling that keeps revealing new content
produces new states (so it is never a repeat), and a batch of edits of different
files has different action signatures.

The module has no imports from the rest of MemuAI so the CLI, the server and
tests can all use it.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

PREF_KEY = "stuck_sensitivity"
SENSITIVITIES = ("off", "low", "normal", "high")
DEFAULT_SENSITIVITY = "normal"
DECISIONS = ("continue", "hint", "replan", "stop")
PAUSE_TIMEOUT = 30 * 60

PROFILES: Dict[str, Dict[str, int]] = {
    "low": {"same_action": 6, "hint_at": 3, "revisit_repeat": 9, "cycles": 4, "same_error": 5,
            "no_new_state": 14, "waiting": 24, "stalled": 7, "no_evidence": 7, "budget": 24, "window": 30},
    "normal": {"same_action": 4, "hint_at": 2, "revisit_repeat": 6, "cycles": 3, "same_error": 3,
               "no_new_state": 10, "waiting": 16, "stalled": 5, "no_evidence": 5, "budget": 18, "window": 24},
    "high": {"same_action": 3, "hint_at": 2, "revisit_repeat": 4, "cycles": 2, "same_error": 2,
             "no_new_state": 7, "waiting": 10, "stalled": 3, "no_evidence": 3, "budget": 12, "window": 18},
}

PASSIVE_ACTIONS = {"wait", "sleep", "poll", "screenshot", "snapshot", "observe", "status", "read_state"}

_IGNORED_KEYS = {"reason", "report", "thought", "thoughts", "summary", "explanation", "id", "call_id", "tool_call_id",
                 "description", "rationale", "comment", "note"}
_COORD_KEYS = {"x", "y", "x1", "y1", "x2", "y2"}
_PERCEPTUAL_TOLERANCE = 1.0

REASONS: Dict[str, tuple] = {
    "repeat": ("Повторяется одно и то же действие без результата",
               "The same action keeps repeating with no effect"),
    "oscillation": ("Агент ходит по кругу между одними и теми же шагами",
                    "The agent is going in circles between the same steps"),
    "same_error": ("Одна и та же ошибка повторяется",
                   "The same error keeps coming back"),
    "no_new_state": ("Несколько шагов подряд ничего нового не происходит",
                     "Several steps in a row bring nothing new"),
    "waiting": ("Ожидание затянулось: состояние давно не меняется",
                "The wait drags on: nothing has changed for a long time"),
    "plan_stalled": ("Шаг плана не продвигается",
                     "The plan step is not moving forward"),
    "no_evidence": ("Модель сообщает о прогрессе, но ничего не меняется",
                    "The model reports progress, but nothing actually changes"),
    "budget": ("Много шагов без новых результатов",
               "Many steps without any new result"),
    "model_signal": ("Модель сама сообщила, что застряла",
                     "The model itself said it is stuck"),
}
_MODEL_HINTS = {
    "repeat": "You are repeating the same action and nothing changes. Do something different "
              "(another element, a keyboard shortcut, scroll, another page) or finish.",
    "oscillation": "You are alternating between the same few actions and states. Stop the cycle: "
                   "choose a different approach or finish.",
    "same_error": "The same error keeps happening. Do not retry the same way: change the approach.",
    "no_new_state": "Your last steps did not reach anything new. Change the approach.",
    "waiting": "Waiting has not changed anything for a long time. Do something else or finish.",
    "plan_stalled": "The current plan step has not advanced. Verify what blocks it and take a different action.",
    "no_evidence": "You keep reporting progress without any observable change. Produce verifiable results.",
    "budget": "Many steps without new results. Re-evaluate the approach.",
    "model_signal": "",
}

_TOOL_MODEL_HINTS = {
    "repeat": "You are repeating the same tool call and nothing changes. Use the result you already have, try a "
              "different approach (other arguments, another tool) or finish and tell the user what blocks you.",
}
_TOOL_AREAS = ("cli", "work", "work_tools", "tools")

def normalize_sensitivity(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in SENSITIVITIES else DEFAULT_SENSITIVITY

def normalize_settings(prefs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {PREF_KEY: normalize_sensitivity((prefs or {}).get(PREF_KEY))}

def _norm_text(value: Any, limit: int = 300) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().lower()[:limit]

def _norm_value(key: str, value: Any) -> Any:
    if key in _COORD_KEYS:
        try:
            return int(round(float(value) / 12.0))
        except (TypeError, ValueError):
            return _norm_text(value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    if isinstance(value, dict):
        return {str(k): _norm_value(str(k), v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
                if str(k) not in _IGNORED_KEYS}
    if isinstance(value, (list, tuple)):
        return [_norm_value("", v) for v in list(value)[:50]]
    return _norm_text(value)

def action_signature(action: Any) -> str:
    """A short stable hash of an action with its arguments (free-text reasons ignored, coordinates bucketed,
    text whitespace/case-folded)."""
    if isinstance(action, dict):
        data = _norm_value("", action)
    else:
        data = _norm_text(action, 2000)
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]

def action_kind(action: Any) -> str:
    if isinstance(action, dict):
        return _norm_text(action.get("action") or action.get("tool") or action.get("name") or action.get("kind") or "", 60)
    return _norm_text(action, 60).split(" ")[0] if action else ""

def state_key(state: Any) -> Any:
    """``None`` (unknown), a list of ints (perceptual image signature, compared with a tolerance) or a hash."""
    if state is None or state == "":
        return None
    if isinstance(state, (list, tuple)) and state and all(isinstance(v, int) for v in state):
        return [int(v) for v in state]
    if isinstance(state, (list, tuple)) and state and all(isinstance(v, (list, tuple)) for v in state):

        flat: List[int] = []
        for item in state:
            flat.extend(int(v) for v in item if isinstance(v, (int, float)))
        return flat or None
    if isinstance(state, str) and re.fullmatch(r"[0-9a-f]{8,64}", state):
        return state
    raw = json.dumps(state, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]

def same_state(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b) or not a:
            return False
        return sum(abs(x - y) for x, y in zip(a, b)) / float(len(a)) < _PERCEPTUAL_TOLERANCE
    return a == b

def normalize_error(error: Any) -> str:
    if not error:
        return ""
    text = _norm_text(error, 400)
    text = re.sub(r"0x[0-9a-f]+|\b[0-9a-f]{8,}\b", "#", text)
    text = re.sub(r"\d+", "#", text)
    return text[:160]

@dataclass
class Step:
    sig: str
    kind: str
    label: str
    state: Any = None
    passive: bool = False
    error: str = ""
    progress: Any = None
    progressed: Optional[bool] = None
    claimed: bool = False
    files_changed: int = 0
    novel: bool = False
    key: str = ""
    ts: float = field(default_factory=time.time)

@dataclass
class Verdict:
    level: str = "ok"
    reason: str = ""
    model_hint: str = ""
    steps: List[Dict[str, Any]] = field(default_factory=list)
    count: int = 0
    step_index: int = 0
    detail: str = ""
    detail_en: str = ""
    area: str = ""
    id: str = ""

    @property
    def paused(self) -> bool:
        return self.level == "pause"

    def title(self, lang: str = "ru") -> str:
        pair = REASONS.get(self.reason) or ("Агент, похоже, застрял", "The agent seems to be stuck")
        return pair[1] if lang == "en" else pair[0]

    def to_card(self) -> Dict[str, Any]:
        """What every UI (main window, Quick Access, Telegram, CLI) shows."""
        return {
            "id": self.id, "kind": "stuck", "reason": self.reason, "area": self.area,
            "title": self.title("ru"), "title_en": self.title("en"),
            "detail": self.detail, "detail_en": self.detail_en,
            "steps": [dict(item) for item in self.steps], "count": self.count, "step": self.step_index,
            "options": list(DECISIONS),
        }

class StuckDetector:
    """One per run.  Not thread-safe; a loop owns it."""

    def __init__(self, sensitivity: Any = DEFAULT_SENSITIVITY, *, area: str = "") -> None:
        self.sensitivity = normalize_sensitivity(sensitivity)
        self.area = area
        self.steps: List[Step] = []
        self.baseline = 0
        self.scale = 1.0
        self.pauses = 0
        self.decisions: List[str] = []
        self._seen: List[Any] = []
        self._last_progress: Any = None

    @property
    def enabled(self) -> bool:
        return self.sensitivity != "off"

    def limit(self, name: str) -> int:
        profile = PROFILES.get(self.sensitivity) or PROFILES[DEFAULT_SENSITIVITY]
        base = profile[name]
        if name in ("hint_at", "window"):
            return base
        return max(base, int(round(base * self.scale)))

    def observe(self, action: Any, *, state: Any = None, label: str = "", error: Any = None,
                passive: Optional[bool] = None, progress: Any = None, progressed: Optional[bool] = None,
                claimed: bool = False, files_changed: int = 0, model_stuck: bool = False,
                model_reason: str = "") -> Verdict:
        """Record the next step (before it is executed) and judge the run."""
        kind = action_kind(action)
        key = state_key(state)
        novel = key is not None and not any(same_state(key, old) for old in self._seen)
        if novel:
            self._seen.append(key)
            if len(self._seen) > 400:
                self._seen = self._seen[-400:]
        step = Step(sig=action_signature(action), kind=kind, label=(label or self._label(action))[:160], state=key,
                    passive=(kind in PASSIVE_ACTIONS) if passive is None else bool(passive),
                    error=normalize_error(error), progress=None if progress is None else state_key(progress) or progress,
                    progressed=progressed, claimed=bool(claimed), files_changed=max(0, int(files_changed or 0)),
                    novel=novel)
        if isinstance(action, dict):
            step.key = _norm_text(action.get("key") or action.get("keys") or action.get("combo") or "", 40)
        self.steps.append(step)
        if len(self.steps) > 600:
            cut = len(self.steps) - 600
            self.steps = self.steps[cut:]
            self.baseline = max(0, self.baseline - cut)
        if not self.enabled:
            return Verdict(area=self.area)
        if model_stuck:
            text = str(model_reason or "").strip()[:400]
            return self._verdict("pause", "model_signal", 1, detail=text, detail_en=text)
        return self._judge()

    def note_result(self, *, error: Any = None, files_changed: int = 0, progressed: Optional[bool] = None) -> None:
        """The outcome of the step just executed (an error, files written)."""
        if not self.steps:
            return
        last = self.steps[-1]
        if error:
            last.error = normalize_error(error)
        if files_changed:
            last.files_changed += int(files_changed)
        if progressed is not None:
            last.progressed = progressed

    def acknowledge(self, decision: str) -> None:
        """The user answered a pause.  Rules restart from here; "continue" also makes them more patient."""
        decision = decision if decision in DECISIONS else "continue"
        self.decisions.append(decision)
        self.baseline = len(self.steps)
        if decision == "continue":
            self.scale = min(3.0, self.scale + 0.5)

    def reset(self) -> None:
        self.steps.clear()
        self._seen.clear()
        self.baseline = 0

    def _window(self) -> List[Step]:
        recent = self.steps[self.baseline:]
        return recent[-self.limit("window") * 2:]

    def _judge(self) -> Verdict:
        steps = self._window()
        if not steps:
            return Verdict(area=self.area)
        cur = steps[-1]
        hint: Optional[Verdict] = None

        if not cur.passive:
            run = 1
            for prev, nxt in zip(reversed(steps[:-1]), reversed(steps[1:])):
                if prev.sig != cur.sig or prev.passive:
                    break
                unchanged = same_state(prev.state, nxt.state) if (prev.state is not None and nxt.state is not None) \
                    else (prev.state is None and nxt.state is None and not nxt.files_changed and not prev.files_changed
                          and prev.progressed is not True and prev.progress is None and nxt.progress is None
                          and not prev.claimed and not nxt.claimed)

                if not unchanged:
                    break
                run += 1

            factor = 2 if _progressive(cur) else 1
            if run >= self.limit("same_action") * factor:
                return self._verdict("pause", "repeat", run, tail=run)
            if run >= max(2, self.limit("hint_at")) * factor:
                hint = self._verdict("hint", "repeat", run, tail=run)

            same = [s for s in steps[-self.limit("window"):] if s.sig == cur.sig and not s.novel and not s.passive
                    and not s.files_changed]
            if cur.state is not None and not cur.novel and len(same) >= self.limit("revisit_repeat") * factor:
                return self._verdict("pause", "repeat", len(same), tail=min(len(steps), len(same) * 2))

        for period in (2, 3):
            cycles = self.limit("cycles")
            need = period * cycles
            if len(steps) < need:
                continue
            tail = steps[-need:]
            if any(s.passive for s in tail) or len({s.sig for s in tail[:period]}) < 2:
                continue
            ok = True
            for i in range(period, need):
                a, b = tail[i], tail[i - period]
                if a.sig != b.sig:
                    ok = False
                    break
                if a.state is not None and b.state is not None and not same_state(a.state, b.state):
                    ok = False
                    break
                if a.files_changed or a.progressed is True:
                    ok = False
                    break
            if ok:
                return self._verdict("pause", "oscillation", cycles, tail=need, period=period)

        errors = [s.error for s in steps[-self.limit("window"):] if s.error]
        last_error = next((s.error for s in reversed(steps) if s.error), "")
        if last_error and steps[-2:] and any(s.error == last_error for s in steps[-2:]):
            count = errors.count(last_error)
            if count >= self.limit("same_error"):
                return self._verdict("pause", "same_error", count, tail=min(len(steps), self.limit("window")),
                                     detail=last_error, detail_en=last_error)

        if cur.passive and cur.state is not None:
            waited = 0
            for s in reversed(steps):
                if not s.passive or not same_state(s.state, cur.state):
                    break
                waited += 1
            if waited >= self.limit("waiting"):
                return self._verdict("pause", "waiting", waited, tail=waited)

        active = [s for s in steps if not s.passive and not _progressive(s)]
        streak = 0
        for s in reversed(active):
            if s.state is None or s.novel or s.files_changed or s.progressed is True:
                break
            streak += 1
        if streak >= self.limit("no_new_state"):
            return self._verdict("pause", "no_new_state", streak, tail=streak)

        if cur.progress is not None:
            stalled = 0
            for s in reversed(steps):
                if s.progress is None or not _same_progress(s.progress, cur.progress) or s.files_changed \
                        or s.progressed is True:
                    break
                stalled += 1
            if stalled >= self.limit("stalled"):
                return self._verdict("pause", "plan_stalled", stalled, tail=stalled)

        claims = 0
        for s in reversed(steps):
            evidence = s.files_changed or s.novel or s.progressed is True
            if not s.claimed or evidence:
                break
            claims += 1
        if claims >= self.limit("no_evidence"):
            return self._verdict("pause", "no_evidence", claims, tail=claims)

        observable = any(s.state is not None or s.progress is not None or s.progressed is not None
                         for s in steps)
        if observable:
            since = 0
            for s in reversed(steps):
                if s.novel or s.files_changed or s.progressed is True:
                    break
                since += 1
            if since >= self.limit("budget"):
                return self._verdict("pause", "budget", since, tail=min(since, 10))
        return hint or Verdict(area=self.area)

    def _verdict(self, level: str, reason: str, count: int, *, tail: int = 6, period: int = 0,
                 detail: str = "", detail_en: str = "") -> Verdict:
        steps = self.steps[self.baseline:][-max(1, min(tail, 12)):]
        hint = (_TOOL_MODEL_HINTS.get(reason) if self.area in _TOOL_AREAS else None) or _MODEL_HINTS.get(reason, "")
        verdict = Verdict(level=level, reason=reason, model_hint=hint,
                          steps=summarize_steps(steps), count=count, step_index=len(self.steps),
                          area=self.area, id=uuid.uuid4().hex[:10])
        if not detail and not detail_en:
            detail, detail_en = _detail(reason, count, period)
        verdict.detail, verdict.detail_en = detail, detail_en
        if level == "pause":
            self.pauses += 1
        return verdict

    @staticmethod
    def _label(action: Any) -> str:
        if isinstance(action, dict):
            kind = action_kind(action) or "?"
            args = {k: v for k, v in action.items() if k not in _IGNORED_KEYS and k not in ("action", "tool", "name")}
            text = json.dumps(args, ensure_ascii=False, default=str)
            return f"{kind} {text}" if args else kind
        return str(action or "")

_NAV_KEYS = re.compile(r"(?i)^(arrow\w*|up|down|left|right|tab|pagedown|pageup|pgdn|pgup|space|j|k)$")

def _progressive(step: Step) -> bool:
    if step.kind in ("scroll", "scroll_down", "scroll_up", "wheel", "next", "next_page"):
        return True
    if step.kind in ("key", "press", "keys") and _NAV_KEYS.match(step.key or ""):
        return True
    return False

def _same_progress(a: Any, b: Any) -> bool:
    return a == b

def _ru_times(n: int) -> str:
    """«1 раз», «3 раза», «5 раз»."""
    n = abs(int(n))
    return "раза" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "раз"

def _detail(reason: str, count: int, period: int) -> tuple:
    if reason == "repeat":
        return (f"Одно и то же действие — {count} {_ru_times(count)} подряд, а состояние не меняется.",
                f"The same action {count} times in a row while nothing changes.")
    if reason == "oscillation":
        return (f"Цикл из {period or 2} шагов повторился {count} {_ru_times(count)}.",
                f"A cycle of {period or 2} steps repeated {count} times.")
    if reason == "same_error":
        return (f"Ошибка повторилась {count} {_ru_times(count)}.", f"The error happened {count} times.")
    if reason == "waiting":
        return (f"{count} ожиданий подряд, состояние не меняется.", f"{count} waits in a row and nothing changes.")
    if reason == "plan_stalled":
        return (f"{count} итераций на одном шаге плана без изменений в файлах.",
                f"{count} iterations on the same plan step without file changes.")
    if reason == "no_evidence":
        return (f"{count} {_ru_times(count)} подряд модель говорит «продолжаю», но файлы и состояние не меняются.",
                f"{count} times in a row the model says “continuing”, but files and state stay the same.")
    if reason == "no_new_state":
        return (f"{count} шагов подряд без нового состояния.", f"{count} steps in a row without a new state.")
    if reason == "budget":
        return (f"{count} шагов без новых результатов.", f"{count} steps without new results.")
    return ("", "")

def summarize_steps(steps: Iterable[Step]) -> List[Dict[str, Any]]:
    """Collapse runs of identical steps: [{label, count, error?}]."""
    out: List[Dict[str, Any]] = []
    last_sig = None
    for step in steps:
        if out and step.sig == last_sig:
            out[-1]["count"] += 1
            if step.error:
                out[-1]["error"] = step.error
            continue
        row: Dict[str, Any] = {"label": step.label, "count": 1}
        if step.error:
            row["error"] = step.error
        out.append(row)
        last_sig = step.sig
    return out[-8:]

_DECISION_WORDS = {
    "continue": ("continue", "продолжить", "продолжай", "продолжи", "да", "yes", "go on"),
    "hint": ("hint", "подсказка", "с подсказкой", "continue with a hint", "продолжить с подсказкой"),
    "replan": ("replan", "change approach", "change the approach", "сменить подход", "другой подход",
               "перепланировать", "re-plan"),
    "stop": ("stop", "стоп", "остановить", "остановись", "нет", "no", "cancel", "отмена"),
}

def choices(lang: str = "ru") -> List[str]:
    if lang == "en":
        return ["Continue", "Continue with a hint", "Change approach", "Stop"]
    return ["Продолжить", "Продолжить с подсказкой", "Сменить подход", "Остановить"]

def parse_decision(payload: Any) -> Dict[str, str]:
    """``{"decision": ..., "hint": ...}`` from a UI payload, a choice label or free text.

    Free text that is not one of the choices is a hint ("try the search box")."""
    if isinstance(payload, dict):
        decision = str(payload.get("decision") or "").strip().lower()
        hint = str(payload.get("hint") or "").strip()[:2000]
        if decision in DECISIONS:
            if decision == "hint" and not hint:
                hint = str(payload.get("answer") or "").strip()[:2000]
            return {"decision": decision if (decision != "hint" or hint) else "continue", "hint": hint}
        if payload.get("approve") is False:
            return {"decision": "stop", "hint": ""}
        text = str(payload.get("answer") or "").strip()
    else:
        text = str(payload or "").strip()
    low = _norm_text(text, 200)
    for decision in ("hint", "replan", "stop", "continue"):
        if low in _DECISION_WORDS[decision]:
            return {"decision": "continue" if decision == "hint" else decision, "hint": ""}
    if not low:
        return {"decision": "continue", "hint": ""}
    return {"decision": "hint", "hint": text[:2000]}

def model_note(decision: str, hint: str = "", verdict: Optional[Verdict] = None) -> str:
    """The English line the model reads after the user's decision."""
    what = ""
    if verdict is not None:
        what = "; ".join(f"{row['label']}" + (f" ×{row['count']}" if row.get("count", 1) > 1 else "")
                         for row in verdict.steps[-4:])
        what = f" (repeating: {what[:400]})" if what else ""
    if decision == "hint":
        return (f"PAUSED as stuck{what}. The user looked at it and gave a hint — follow it: {hint[:1500]}")
    if decision == "replan":
        return (f"PAUSED as stuck{what}. The user asked to CHANGE THE APPROACH: the current strategy did not work. "
                "Make a new plan that does not repeat the previous actions, then continue."
                + (f" User note: {hint[:1000]}" if hint else ""))
    if decision == "stop":
        return f"PAUSED as stuck{what}. The user stopped the task."
    return (f"PAUSED as stuck{what}. The user chose to continue: take a fresh look and try again, "
            "but do not repeat an action that changed nothing.")

def stop_message(verdict: Optional[Verdict], lang: str = "ru") -> str:
    title = verdict.title(lang) if verdict else ("Агент застрял" if lang != "en" else "The agent got stuck")
    return (f"Остановлено пользователем после паузы: {title.lower()}" if lang != "en"
            else f"Stopped by the user after a pause: {title[0].lower() + title[1:]}")

def timeout_message(lang: str = "ru") -> str:
    return ("Агент зациклился и был на паузе, но ответа не было — работа остановлена" if lang != "en"
            else "The agent is stuck: it was paused and nobody answered, so the work stopped")

def telegram_text(card: Dict[str, Any], lang: str = "ru") -> str:
    """A plain-text version of the card for Telegram / the CLI."""
    en = lang == "en"
    lines = [("⏸ " + (card.get("title_en") if en else card.get("title") or "")).strip()]
    detail = card.get("detail_en") if en else card.get("detail")
    if detail:
        lines.append(str(detail))
    for row in (card.get("steps") or [])[-6:]:
        count = int(row.get("count") or 1)
        lines.append(f"• {row.get('label', '')}" + (f" ×{count}" if count > 1 else ""))
    lines.append("Continue / hint / change approach / stop?" if en
                 else "Продолжить / подсказка / сменить подход / остановить?")
    return "\n".join(line for line in lines if line)

def cli_text(card: Dict[str, Any], lang: str = "en") -> str:
    """The card as Markdown for the MemuCode TUI question dialog (the dialog lists the choices itself).
    ``telegram_text`` joined by single line breaks ran together into one paragraph once rendered as Markdown."""
    en = lang == "en"
    parts = ["**⏸ " + str((card.get("title_en") if en else card.get("title")) or "").strip() + "**"]
    detail = card.get("detail_en") if en else card.get("detail")
    if detail:
        parts.append(str(detail))
    rows = []
    for row in (card.get("steps") or [])[-6:]:
        count = int(row.get("count") or 1)
        label = str(row.get("label", "")).replace("`", "'")
        rows.append(f"- `{label}`" + (f" ×{count}" if count > 1 else "") + (" — error" if row.get("error") and en else
                                                                               " — ошибка" if row.get("error") else ""))
    if rows:
        parts.append(("Last steps:" if en else "Последние шаги:") + "\n\n" + "\n".join(rows))
    parts.append("Nothing runs until you choose. Your own text becomes a hint for the model." if en else
                 "Пока вы не решите, ничего не выполняется. Свой текст — подсказка для модели.")
    return "\n\n".join(part for part in parts if part)

class StuckGate:
    """A pause point for loops that run inside the server (Work Browser, Computer Use).

    The loop awaits :meth:`wait`; the UI reads :meth:`public` (``state.stuck``) and answers via :meth:`decide`.
    Nothing runs while the loop waits, and cancelling the run releases the wait."""

    def __init__(self) -> None:
        self.card: Optional[Dict[str, Any]] = None
        self._future: Optional[asyncio.Future] = None

    def public(self) -> Optional[Dict[str, Any]]:
        return dict(self.card) if self.card else None

    @property
    def waiting(self) -> bool:
        return self._future is not None and not self._future.done()

    async def wait(self, card: Dict[str, Any], timeout: float = PAUSE_TIMEOUT) -> Dict[str, str]:
        self.card = dict(card)
        self._future = asyncio.get_running_loop().create_future()
        try:
            return await asyncio.wait_for(asyncio.shield(self._future), timeout)
        except asyncio.TimeoutError:
            return {"decision": "timeout", "hint": ""}
        finally:
            self.card = None
            self._future = None

    def decide(self, payload: Any) -> Dict[str, str]:
        if not self.waiting:
            raise RuntimeError("nothing is paused")
        if isinstance(payload, dict) and payload.get("card_id") and self.card \
                and str(payload["card_id"]) != str(self.card.get("id")):
            raise RuntimeError("this pause is already over")
        decision = parse_decision(payload)
        assert self._future is not None
        self._future.set_result(decision)
        return decision

    def release(self) -> None:
        """Cancel/stop: wake the loop with "stop"."""
        if self.waiting and self._future is not None:
            self._future.set_result({"decision": "stop", "hint": ""})

def gate_of(runtime: Any) -> StuckGate:
    gate = getattr(runtime, "stuck_gate", None)
    if not isinstance(gate, StuckGate):
        gate = StuckGate()
        try:
            setattr(runtime, "stuck_gate", gate)
        except Exception:
            pass
    return gate

def public_pause(runtime: Any) -> Optional[Dict[str, Any]]:
    gate = getattr(runtime, "stuck_gate", None)
    return gate.public() if isinstance(gate, StuckGate) else None

CLI_PASSIVE_TOOLS = {"BashOutput", "TaskOutput"}
CLI_FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

def cli_round(calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    """One MemuCode tool round as a detector step: ``{"action", "label", "passive"}``."""
    real = [c for c in calls if str(c.get("name") or "") != "__invalid__"]
    action = {"action": "tools", "calls": sorted(
        (json.dumps({"tool": str(c.get("name") or ""), "args": _norm_value("", c.get("args") or {})},
                    ensure_ascii=False, sort_keys=True, default=str) for c in real))}
    names = [str(c.get("name") or "") for c in real]
    label_parts = []
    for c in real[:4]:
        args = c.get("args") if isinstance(c.get("args"), dict) else {}
        what = args.get("command") or args.get("file_path") or args.get("path") or args.get("pattern") \
            or args.get("url") or args.get("query") or ""
        label_parts.append(f"{c.get('name')}" + (f" {str(what)[:70]}" if what else ""))
    return {"action": action, "label": "; ".join(label_parts) or "tools",
            "passive": bool(names) and all(name in CLI_PASSIVE_TOOLS for name in names)}

def cli_outcome(pairs: Iterable[Any]) -> Dict[str, Any]:
    """Files changed and the first error of an executed round (``[(ToolCall, ToolResult)]``)."""
    files, error = 0, ""
    for call, result in pairs:
        if getattr(result, "ok", False) and getattr(call, "name", "") in CLI_FILE_TOOLS:
            files += 1
        if not getattr(result, "ok", True) and not error:
            error = str(getattr(result, "error", "") or "")[:300]
    return {"files_changed": files, "error": error}
