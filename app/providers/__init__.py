"""Реестр провайдеров MemuAI Proxy."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .antigravity import AntigravityProvider
from .base import Provider
from .custom import CustomProvider
from .geminiweb import GeminiWebProvider
from .genspark import GensparkProvider
from .grok import GrokProvider
from .kiro import KiroProvider
from .local import LocalProvider
from .opencode import OpenCodeProvider

_providers: Dict[str, Provider] = {}

# Порядок важен: он же определяет порядок плиток в панели.
BUILTIN_CLASSES = (
    GensparkProvider, KiroProvider, AntigravityProvider,
    GeminiWebProvider, GrokProvider, OpenCodeProvider, LocalProvider,
)

# Имена, которые нельзя занять кастомным подключением.
RESERVED_PREFIXES = ("genspark", "kiro", "antigravity", "geminiweb",
                     "notion", "grok", "zcode", "opencode", "custom", "local")


def init_registry() -> Dict[str, Provider]:
    """Создаёт экземпляры провайдеров (один на процесс)."""
    for cls in BUILTIN_CLASSES:
        _providers[cls.id] = cls()
    # custom — один экземпляр обслуживает все кастомные подключения
    _providers["custom"] = CustomProvider()
    return _providers


async def reset_registry() -> None:
    """Закрывает сетевые ресурсы провайдеров и пересоздаёт их.

    Используется «мягким перезапуском» панели: после долгой работы у провайдера
    могут остаться зависшие сессии curl_cffi, из-за которых запросы висят.
    """
    for provider in list(_providers.values()):
        try:
            await provider.close()
        except Exception:
            pass
    _providers.clear()
    init_registry()


def get(provider_id: str) -> Optional[Provider]:
    return _providers.get(provider_id)


def all_providers() -> Dict[str, Provider]:
    return _providers


def builtin_ids() -> List[str]:
    return [cls.id for cls in BUILTIN_CLASSES] + ["custom"]


def meta_list() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for pid in builtin_ids():
        p = _providers.get(pid)
        if p is None:
            continue
        out.append({
            "id": pid, "title": p.title, "icon": p.icon, "oauth": p.oauth,
            "description": p.description,
            "title_en": p.title_en or p.title,
            "description_en": p.description_en or p.description,
            "fields": [f.public_dict() for f in p.account_fields()],
            "native_search": bool(getattr(p, "native_search", False)),
            "managed": bool(getattr(p, "managed", False)),
            "settings": [f.public_dict() for f in settings_schema(p)],
        })
    return out


def settings_schema(provider) -> List[Any]:
    """Настройки провайдера: общие для всех плюс его собственные."""
    from .base import Field
    common = [
        Field("web_search", "Веб-поиск", "select", required=False, default="auto",
              options=["auto", "always", "off"],
              help="auto — по флагу web_search в запросе; always — всегда, даже если "
                   "клиент не просил (удобно для сторонних программ и командной "
                   "строки); off — никогда."
                   + ("" if getattr(provider, "native_search", False)
                      else " У этого провайдера поиск выполняет сам MemuAI Proxy."),
              label_en="Web search",
              help_en="auto — follow the web_search flag of the request; always — force "
                      "it on even when the client did not ask (handy for third-party "
                      "apps and the command line); off — never."
                      + ("" if getattr(provider, "native_search", False)
                         else " For this provider MemuAI Proxy runs the search itself.")),
    ]
    return common + list(provider.settings_fields())
