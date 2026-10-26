"""What the user says about their models, and how it is used.

Three settings (all machine-wide, kept in the user settings file):

* ``model_notes`` — a remark per model: "fast, good at CSS", "slow but careful with algorithms". They are
  shown in every model picker and given to whoever has to *choose* a model: the decision model of
  ``/model auto``, the main model when it starts an agent, the agent picker.
* ``model_routing`` — ``/model auto``: several models per *tier* (``fast`` for simple requests, ``strong`` for
  real work, optionally ``balanced`` between them). The older ``fast`` / ``strong`` single model keys are
  still read.
* ``worker_models`` — the models agents may run on. With more than one the model that starts an agent (or the
  decision model, or a keyword match against the notes) picks one per agent.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

TIERS = ("fast", "balanced", "strong")
NOTE_LIMIT = 300

def model_notes(settings: Any) -> Dict[str, str]:
    raw = settings.get("model_notes") if settings is not None else None
    if not isinstance(raw, dict):
        return {}
    notes: Dict[str, str] = {}
    for key, value in raw.items():
        text = " ".join(str(value or "").split())[:NOTE_LIMIT]
        if str(key).strip() and text:
            notes[str(key).strip()] = text
    return notes

def note_for(settings: Any, model_id: str) -> str:
    return model_notes(settings).get(str(model_id), "")

def set_model_note(settings: Any, model_id: str, text: str) -> None:
    notes = model_notes(settings)
    text = " ".join(str(text or "").split())[:NOTE_LIMIT]
    if text:
        notes[str(model_id)] = text
    else:
        notes.pop(str(model_id), None)
    settings.set("model_notes", notes)

def routing_config(settings: Any) -> Dict[str, Any]:
    """``model_routing`` with the older ``fast`` / ``strong`` keys folded into ``tiers``."""
    raw = settings.get("model_routing") if settings is not None else None
    raw = raw if isinstance(raw, dict) else {}
    tiers: Dict[str, List[str]] = {tier: [] for tier in TIERS}
    given = raw.get("tiers") if isinstance(raw.get("tiers"), dict) else {}
    for tier in TIERS:
        values = given.get(tier)
        if isinstance(values, str):
            values = [values]
        tiers[tier] = _unique(str(item) for item in (values or []) if str(item).strip())
    for tier in ("fast", "strong"):
        legacy = str(raw.get(tier) or "").strip()
        if legacy and legacy not in tiers[tier]:
            tiers[tier].append(legacy)
    return {"enabled": bool(raw.get("enabled")), "tiers": tiers,
            "balanced_from": float(raw.get("balanced_from") or 0.33), "strong_from": float(raw.get("strong_from") or 0.62)}

def worker_models(settings: Any) -> List[str]:
    """Models agents may use; the single ``worker_model`` of earlier versions counts as a list of one."""
    raw = settings.get("worker_models") if settings is not None else None
    rows = [str(item) for item in raw] if isinstance(raw, list) else []
    legacy = str(settings.get("worker_model") or "").strip() if settings is not None else ""
    if legacy and legacy not in rows:
        rows.insert(0, legacy)
    return _unique(row for row in rows if row.strip())

def _unique(values: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(values))

_WORD = re.compile(r"[\w#+.\-]{3,}", re.UNICODE)
_STOP = {"the", "and", "for", "with", "this", "that", "from", "into", "что", "для", "это", "как", "при", "или", "над"}

def _words(text: str) -> set:
    return {word.lower() for word in _WORD.findall(text or "") if word.lower() not in _STOP}

def pick_by_notes(task: str, candidates: List[str], notes: Dict[str, str], *, extra: Optional[Dict[str, str]] = None
                  ) -> Tuple[str, str]:
    """(model, why) — the candidate whose remark shares the most words with the task.

    A cheap, offline fallback for when no decision model is connected. With no overlap at all the first
    candidate wins (the order the user gave is the order of preference). ``extra`` adds text per model that
    counts like the remark (its name, the provider, features)."""
    if not candidates:
        return "", ""
    wanted = _words(task)
    best, best_score, shared = candidates[0], 0.0, set()
    for model in candidates:
        text = notes.get(model, "") + " " + (extra or {}).get(model, "")
        overlap = wanted & _words(text)
        if len(overlap) > best_score:
            best, best_score, shared = model, float(len(overlap)), overlap
    if best_score:
        return best, "note: " + ", ".join(sorted(shared)[:4])
    return best, ""

def describe_candidates(candidates: List[str], notes: Dict[str, str]) -> Dict[str, str]:
    """``{model: remark}`` for the choice question of a decision model (a model without a remark says so)."""
    return {model: (notes.get(model) or "no remark from the user") for model in candidates}
