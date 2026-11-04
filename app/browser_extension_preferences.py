"""Atomic shared model choices for the desktop and connected browser panel."""
from __future__ import annotations

from . import pgdata
from .storage import mutate_json

def _model(value):
    if not isinstance(value, str) or len(value) > 512 or any(ord(char) < 32 for char in value):
        raise ValueError("Некорректный идентификатор модели")
    return value

def update_models(body):
    changes = {}
    if "default_model" in body:
        changes["default_model"] = _model(body["default_model"])
    if "pinned_models" in body:
        values = body["pinned_models"]
        if not isinstance(values, list) or len(values) > 500:
            raise ValueError("Нужен список закреплённых моделей")
        changes["pinned_models"] = list(dict.fromkeys(_model(value) for value in values if value != ""))
    pin = _model(body["pin_model"]) if "pin_model" in body else None
    if pin is not None and (not pin or not isinstance(body.get("pinned"), bool)):
        raise ValueError("Укажите модель и состояние закрепления")
    if not changes and pin is None:
        raise ValueError("Нет изменений моделей")

    own_model = "default_model" in changes and pgdata._space_prefs_path() is not None
    model = changes.pop("default_model") if own_model else None

    def merge(raw):
        data = pgdata._normalize_preferences(raw)
        data.update(changes)
        if pin is not None:
            values = list(data.get("pinned_models") or [])
            data["pinned_models"] = list(dict.fromkeys([*values, pin])) if body["pinned"] else [value for value in values if value != pin]
        return True, pgdata._normalize_preferences(data)
    if changes or pin is not None:
        mutate_json(pgdata.MEMU_DIR / "preferences.json", {}, merge, backup=False,
                    validator=lambda value: isinstance(value, dict), repair_invalid=True)
    # A non-standard workspace keeps its own default model.
    saved = pgdata.save_preferences({"default_model": model}) if own_model else pgdata.load_preferences()
    return {"default_model": saved.get("default_model") or "", "pinned_models": saved.get("pinned_models") or []}
