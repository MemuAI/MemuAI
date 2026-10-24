"""Маршрутизация моделей: публичный id «<префикс>-<модель>» → провайдер/аккаунт.

Префиксы: id встроенных провайдеров (genspark, kiro, …) и имена кастомных
подключений. Модель без префикса ищется по уникальному совпадению суффикса,
при неоднозначности — через default_provider.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .config import RETIRED_PROVIDER_IDS, config_store
from .state import ModelEntry, state


@dataclass
class Resolved:
    entry: Optional[ModelEntry]
    prefix: str
    model: str
    provider: str


def known_prefixes() -> List[str]:
    """Все активные префиксы: провайдеры с аккаунтами + имена кастомных подключений."""
    from .providers import builtin_ids
    prefixes = set()
    for acc in state.accounts.values():
        if acc.provider == "custom":
            name = str(acc.fields.get("name", "")).strip().lower().replace(" ", "-")
            if name:
                prefixes.add(name)
        elif acc.provider in builtin_ids() and acc.provider != "custom":
            prefixes.add(acc.provider)
    return sorted(prefixes, key=len, reverse=True)


def public_id(prefix: str, model: str) -> str:
    if not config_store.settings.use_prefixes:
        return model
    return f"{prefix}-{model}"


def resolve(model_id: str) -> Optional[Resolved]:
    """Разбирает запрошенную модель. None — не удалось определить."""
    requested = (model_id or "").strip()
    if not requested:
        return None
    # A stale model choice must not fall through to an unrelated default API.
    if any(requested.lower().startswith(provider + "-") for provider in RETIRED_PROVIDER_IDS):
        return None

    entries = state.find_model(requested)
    # Preserve an exact disabled match so the caller can reject it explicitly;
    # otherwise it could fall through to permissive prefix routing.
    if entries is not None:
        provider = _provider_of_prefix(entries.prefix)
        if entries.provider == "custom" and provider != "custom":
            # A stale custom catalog entry must not override an active built-in
            # provider that owns the same reserved prefix.
            return Resolved(None, entries.prefix, entries.model, provider)
        return Resolved(entries, entries.prefix, entries.model, entries.provider)

    # 1) явный префикс
    for prefix in known_prefixes():
        if requested.lower().startswith(prefix.lower() + "-"):
            raw = requested[len(prefix) + 1:]
            if raw:
                provider = _provider_of_prefix(prefix)
                return Resolved(None, prefix, raw, provider)

    # 2) уникальное совпадение суффикса среди каталога
    normalized = requested.lower().replace("_", "-")
    # Только модели живых провайдеров: модель удалённого Genspark не должна
    # перехватывать короткое имя у провайдера, который её реально обслуживает.
    enabled = state.enabled_models()
    matches = [m for m in enabled
               if m.model.lower().replace("_", "-") == normalized or m.id.lower().replace("_", "-") == normalized]
    if not matches:
        matches = [m for m in enabled if m.id.lower().endswith("." + normalized)]
    if len(matches) == 1:
        m = matches[0]
        return Resolved(m, m.prefix, m.model, m.provider)
    if len(matches) > 1:
        default = config_store.settings.default_provider
        if default:
            for m in matches:
                if default in (m.provider, m.prefix):
                    return Resolved(m, m.prefix, m.model, m.provider)
        # A silent arbitrary choice can send data to the wrong provider.  The
        # caller asks the user to add a prefix or configure a default instead.
        return None

    # 3) неизвестная модель — трактуем как модель провайдера по умолчанию
    default = config_store.settings.default_provider
    if default:
        return Resolved(None, default, requested, _provider_of_prefix(default))
    return None


def _provider_of_prefix(prefix: str) -> str:
    from .providers import builtin_ids
    # A legacy custom connection may have a name that now belongs to a built-in
    # provider.  Keep reserved prefixes deterministic instead of silently
    # sending their traffic to an unrelated custom endpoint.
    if prefix.lower() in builtin_ids() and prefix.lower() != "custom" and any(
            account.provider == prefix.lower() for account in state.accounts.values()):
        return prefix.lower()
    for acc in state.accounts.values():
        if acc.provider == "custom":
            name = str(acc.fields.get("name", "")).strip().lower().replace(" ", "-")
            if name == prefix.lower():
                return "custom"
    return prefix
