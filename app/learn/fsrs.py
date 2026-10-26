# -*- coding: utf-8 -*-
"""FSRS-5 scheduler for MemuLearn (round 40) — a faithful port of the reference implementation
(open-spaced-repetition/py-fsrs 4.x, 19 parameters, FSRS-5), with no dependencies.

State machine (as in py-fsrs): a new card is *Learning* (learning steps 1 min → 10 min), graduates to *Review*,
a lapse sends it to *Relearning* (10 min) and back. Stability ``S`` (days until R falls to 90 %) and difficulty
``D`` (1..10) follow the FSRS-5 formulas; a review on the same day uses the short-term stability formula.

Times are unix seconds (float). ``days`` elapsed are whole days like ``timedelta.days`` in the reference.
Fuzz (± a few % of the interval, as Anki/py-fsrs) is deterministic here: seeded by the card id and its review
count, so the interval on a button is exactly the one the card gets.
"""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional, Sequence, Tuple

DEFAULT_PARAMETERS: Tuple[float, ...] = (
    0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604, 0.0046, 1.54575, 0.1192,
    1.01925, 1.9395, 0.11, 0.29605, 2.2698, 0.2315, 2.9898, 0.51655, 0.6621,
)
DECAY = -0.5
FACTOR = 0.9 ** (1 / DECAY) - 1
DAY = 86400.0
FUZZ_RANGES = ((2.5, 7.0, 0.15), (7.0, 20.0, 0.1), (20.0, math.inf, 0.05))

NEW, LEARNING, REVIEW, RELEARNING = 0, 1, 2, 3
AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4
RATINGS = (AGAIN, HARD, GOOD, EASY)

@dataclass
class CardState:
    """Scheduling fields of one card. ``state`` NEW = never reviewed (shown as new, scheduled as Learning step 0)."""
    state: int = NEW
    step: Optional[int] = 0
    stability: Optional[float] = None
    difficulty: Optional[float] = None
    due: float = 0.0
    last_review: Optional[float] = None
    reps: int = 0
    lapses: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, row: Dict[str, Any]) -> "CardState":
        def num(key: str) -> Optional[float]:
            value = row.get(key)
            return None if value is None or value == "" else float(value)
        step = row.get("step")
        return cls(state=int(row.get("state") or 0), step=None if step is None or step == "" else int(step),
                   stability=num("stability"), difficulty=num("difficulty"), due=float(row.get("due") or 0.0),
                   last_review=num("last_review"), reps=int(row.get("reps") or 0), lapses=int(row.get("lapses") or 0))

@dataclass
class Scheduler:
    parameters: Sequence[float] = DEFAULT_PARAMETERS
    desired_retention: float = 0.9
    learning_steps: Sequence[float] = (60.0, 600.0)
    relearning_steps: Sequence[float] = (600.0,)
    maximum_interval: int = 36500
    enable_fuzzing: bool = True
    _w: Tuple[float, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        w = tuple(float(x) for x in self.parameters)
        if len(w) != 19:
            raise ValueError("FSRS-5 needs 19 parameters")
        self._w = w
        self.desired_retention = min(0.99, max(0.7, float(self.desired_retention)))
        self.learning_steps = tuple(float(s) for s in self.learning_steps)
        self.relearning_steps = tuple(float(s) for s in self.relearning_steps)

    @staticmethod
    def retrievability(stability: Optional[float], elapsed_days: float) -> float:
        if not stability:
            return 0.0
        return (1 + FACTOR * max(0.0, elapsed_days) / stability) ** DECAY

    def initial_stability(self, rating: int) -> float:
        return max(self._w[rating - 1], 0.1)

    def initial_difficulty(self, rating: int) -> float:
        return min(max(self._w[4] - math.exp(self._w[5] * (rating - 1)) + 1, 1.0), 10.0)

    def next_interval(self, stability: float) -> int:
        days = (stability / FACTOR) * (self.desired_retention ** (1 / DECAY) - 1)
        return int(min(max(round(days), 1), self.maximum_interval))

    def short_term_stability(self, stability: float, rating: int) -> float:
        return stability * math.exp(self._w[17] * (rating - 3 + self._w[18]))

    def next_difficulty(self, difficulty: float, rating: int) -> float:
        delta = -(self._w[6] * (rating - 3))
        damped = difficulty + (10.0 - difficulty) * delta / 9.0
        reverted = self._w[7] * self.initial_difficulty(EASY) + (1 - self._w[7]) * damped
        return min(max(reverted, 1.0), 10.0)

    def next_forget_stability(self, difficulty: float, stability: float, r: float) -> float:
        w = self._w
        long_term = w[11] * math.pow(difficulty, -w[12]) * (math.pow(stability + 1, w[13]) - 1) * math.exp((1 - r) * w[14])
        short_term = stability / math.exp(w[17] * w[18])
        return min(long_term, short_term)

    def next_recall_stability(self, difficulty: float, stability: float, r: float, rating: int) -> float:
        w = self._w
        hard = w[15] if rating == HARD else 1
        easy = w[16] if rating == EASY else 1
        return stability * (1 + math.exp(w[8]) * (11 - difficulty) * math.pow(stability, -w[9])
                            * (math.exp((1 - r) * w[10]) - 1) * hard * easy)

    def _fuzz(self, days: int, seed: str) -> int:
        if not self.enable_fuzzing or days < 2.5:
            return days
        delta = 1.0
        for start, end, factor in FUZZ_RANGES:
            delta += factor * max(min(days, end) - start, 0.0)
        lo = max(2, int(round(days - delta)))
        hi = min(int(round(days + delta)), self.maximum_interval)
        lo = min(lo, hi)
        fuzzed = random.Random(seed).random() * (hi - lo + 1) + lo
        return int(min(round(fuzzed), self.maximum_interval))

    def review(self, card: CardState, rating: int, now: float, seed: str = "") -> Tuple[CardState, float]:
        """Return ``(new_state, interval_seconds)``. ``card`` is not modified."""
        if rating not in RATINGS:
            raise ValueError("rating must be 1..4")
        c = CardState(**card.to_dict())
        if c.state == NEW:
            c.state, c.step = LEARNING, 0
        days_since = int((now - c.last_review) // DAY) if c.last_review is not None else None
        r = self.retrievability(c.stability, days_since or 0) if c.last_review is not None else 0.0

        if c.stability is None or c.difficulty is None:
            c.stability, c.difficulty = self.initial_stability(rating), self.initial_difficulty(rating)
        elif days_since is not None and days_since < 1:
            c.stability = self.short_term_stability(c.stability, rating)
            c.difficulty = self.next_difficulty(c.difficulty, rating)
        else:
            c.stability = (self.next_forget_stability(c.difficulty, c.stability, r) if rating == AGAIN
                           else self.next_recall_stability(c.difficulty, c.stability, r, rating))
            c.difficulty = self.next_difficulty(c.difficulty, rating)

        interval: float
        if c.state in (LEARNING, RELEARNING):
            steps = self.learning_steps if c.state == LEARNING else self.relearning_steps
            step = int(c.step or 0)
            if not steps or step > len(steps):
                c.state, c.step = REVIEW, None
                interval = self.next_interval(c.stability) * DAY
            elif rating == AGAIN:
                c.step = 0
                interval = steps[0]
            elif rating == HARD:
                if step == 0 and len(steps) == 1:
                    interval = steps[0] * 1.5
                elif step == 0:
                    interval = (steps[0] + steps[1]) / 2.0
                else:
                    interval = steps[step]
            elif rating == GOOD and step + 1 < len(steps):
                c.step = step + 1
                interval = steps[c.step]
            else:
                c.state, c.step = REVIEW, None
                interval = self.next_interval(c.stability) * DAY
        else:
            if rating == AGAIN:
                c.lapses += 1
                if self.relearning_steps:
                    c.state, c.step = RELEARNING, 0
                    interval = self.relearning_steps[0]
                else:
                    interval = self.next_interval(c.stability) * DAY
            else:
                interval = self.next_interval(c.stability) * DAY

        if c.state == REVIEW and interval >= DAY:
            interval = self._fuzz(int(interval // DAY), f"{seed}:{c.reps}") * DAY
        c.reps += 1
        c.last_review = now
        c.due = now + interval
        return c, interval

    def preview(self, card: CardState, now: float, seed: str = "") -> Dict[int, float]:
        """Interval in seconds for each rating — the hint on the four buttons."""
        return {rating: self.review(card, rating, now, seed)[1] for rating in RATINGS}

def format_interval(seconds: float, lang: str = "ru") -> str:
    """«<1 мин», «10 мин», «3 ч», «4 д», «2,1 мес», «1,3 г» (en: «<1m», «10m», «3h», «4d», «2.1mo», «1.3y»)."""
    en = lang == "en"
    s = max(0.0, float(seconds))
    if s < 60:
        return "<1m" if en else "<1 мин"
    if s < 3600:
        return f"{round(s / 60)}m" if en else f"{round(s / 60)} мин"
    if s < DAY:
        return f"{round(s / 3600)}h" if en else f"{round(s / 3600)} ч"
    days = s / DAY
    if days < 30:
        return f"{round(days)}d" if en else f"{round(days)} д"
    if days < 365:
        value = f"{days / 30.4:.1f}"
        return f"{value}mo" if en else f"{value.replace('.', ',')} мес"
    value = f"{days / 365:.1f}"
    return f"{value}y" if en else f"{value.replace('.', ',')} г"
