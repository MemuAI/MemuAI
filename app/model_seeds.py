"""Одноразовые обновления каталога моделей при смене версии MemuAI.

Каталог хранится в ``models.json`` и правится пользователем (выключить модель,
удалить лишнее). Поэтому обновление запускается один раз на версию —
``model_seed_version`` в config.json, — а не при каждом старте: иначе удалённая
вручную модель возвращалась бы после каждого перезапуска.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from .config import config_store
from .state import ModelEntry

log = logging.getLogger("memuai.model_seeds")

#: Поднимается, когда у провайдера меняется форма каталога.
#:   1 — Antigravity: единые имена моделей, уровни размышлений Gemini
#:       (Low / Medium / High), у обоих Claude «-thinking».
#:   2 — Antigravity: уровни размышлений у Gemini 3.7 / 3.8 Flash
#:       (``gemini-3.8-flash-high|medium|low``). Каталог версии 1 получает только их.
SEED_VERSION = 2


def _antigravity_entry(public: str, note: str, template: Any) -> ModelEntry:
    if template is None:
        return ModelEntry("antigravity", "antigravity", public, note=note)
    return ModelEntry(template.prefix, template.provider, public, account_id=template.account_id,
                      enabled=template.enabled, source=template.source, note=note)


def upgrade(state: Any) -> Dict[str, int]:
    """Применяет недостающие обновления каталога. Возвращает счётчики изменений."""
    previous = int(config_store.settings.model_seed_version or 0)
    if previous >= SEED_VERSION:
        return {}
    from .providers import antigravity_models
    report: Dict[str, int] = {}
    with state._lock:
        # Без аккаунта Antigravity каталог ему не нужен: стартовые модели
        # появятся при добавлении первого аккаунта (уже под новыми именами).
        if any(acc.provider == "antigravity" for acc in state.accounts.values()):
            # Каталог, уже приведённый к единым именам, получает только новинки:
            # модель, которую пользователь удалил после прошлого обновления, не возвращается.
            only_new = antigravity_models.level_variants() if previous >= 1 else None
            change = antigravity_models.upgrade_catalog(state.models, _antigravity_entry,
                                                       only_new=only_new, add_seeds=False)
            report = {"renamed": change.renamed, "removed": change.removed, "added": change.added,
                      "noted": change.noted}
            if any(report.values()):
                state.save_models()
                log.info("Antigravity catalogue: %s renamed, %s removed, %s added, %s titled",
                         change.renamed, change.removed, change.added, change.noted)
    config_store.apply_updates({"model_seed_version": SEED_VERSION})
    return report
