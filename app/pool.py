"""Пул аккаунтов: порядок из панели + round-robin + реакция на ошибки."""
from __future__ import annotations

import itertools
import threading
import time
from typing import List

from .config import config_store
from .providers.base import (
    ProviderAuthError,
    ProviderCaptchaError,
    ProviderContentFilterError,
    ProviderModelUnavailableError,
    readable_provider_error,
    safe_provider_error,
)
from .state import ST_INVALID, ST_OK, ST_UNKNOWN, Account, state

# ошибки, которые не должны портить репутацию аккаунта
NO_PENALTY = (ProviderModelUnavailableError, ProviderContentFilterError)


class AccountPool:
    def __init__(self) -> None:
        self._rr: dict = {}
        self._lock = threading.Lock()
        # (account_id, model) → время, до которого модель считается недоступной
        self._model_blocks: dict = {}
        # (account_id, model) → почему (текст провайдера), чтобы показать его вместо общей фразы
        self._block_reasons: dict = {}

    def _next_idx(self, key: str) -> int:
        with self._lock:
            counter = self._rr.get(key)
            if counter is None:
                counter = itertools.count()
                self._rr[key] = counter
            return next(counter)

    # ---------- недоступные модели ----------

    def block_model(self, account: Account, model: str, seconds: float = 3600,
                    reason: str = "") -> None:
        if not model:
            return
        key = (account.id, model.lower())
        with self._lock:
            self._model_blocks[key] = time.time() + seconds
            if reason:
                self._block_reasons[key] = str(reason)[:400]

    def model_blocked(self, account: Account, model: str) -> bool:
        if not model:
            return False
        key = (account.id, model.lower())
        with self._lock:
            until = self._model_blocks.get(key, 0)
            if until and until > time.time():
                return True
            if until:
                self._model_blocks.pop(key, None)
                self._block_reasons.pop(key, None)
        return False

    def block_reason(self, accounts: List[Account], model: str) -> str:
        """Почему модель закрыта для этих аккаунтов (текст провайдера; пусто, если не сохранён)."""
        with self._lock:
            for account in accounts:
                text = self._block_reasons.get((account.id, str(model).lower()))
                if text:
                    return text
        return ""

    def clear_blocks(self) -> None:
        with self._lock:
            self._model_blocks.clear()
            self._block_reasons.clear()

    def clear_account_blocks(self, account_id: str) -> int:
        """Снять блокировки моделей одного аккаунта: его проверили, изменили или починили."""
        with self._lock:
            keys = [k for k in self._model_blocks if k[0] == account_id]
            for key in keys:
                self._model_blocks.pop(key, None)
                self._block_reasons.pop(key, None)
        return len(keys)

    def expire_blocks(self) -> int:
        """Drop expired per-model blocks so the table cannot grow forever."""
        now = time.time()
        with self._lock:
            stale = [k for k, until in self._model_blocks.items() if until <= now]
            for key in stale:
                self._model_blocks.pop(key, None)
                self._block_reasons.pop(key, None)
        return len(stale)

    # ---------- выбор аккаунта ----------

    def ordered_candidates(self, provider: str, model: str = "",
                           prefix: str = "") -> List[Account]:
        ordered = self._ordered_candidates(provider, model, prefix)
        try:  # Round 35 (quota): «Экономить квоту» moves accounts that will run out before the reset back
            from . import quota_planner
            return quota_planner.reorder(ordered)
        except Exception:
            return ordered

    def _ordered_candidates(self, provider: str, model: str = "",
                            prefix: str = "") -> List[Account]:
        accounts = [a for a in state.usable_accounts(provider)
                    if not self.model_blocked(a, model)]
        if provider == "custom" and prefix:
            # Каждое Custom-подключение — отдельный пул. Раньше вращался общий
            # список всех Custom-аккаунтов, а фильтр по имени применялся уже
            # после: при чередовании подключений [A1, B1, A2] аккаунт A2
            # получал вдвое больше запросов, чем A1.
            wanted = prefix.lower()
            accounts = [a for a in accounts
                        if str(a.fields.get("name", "")).strip().lower().replace(" ", "-") == wanted]
        if not accounts:
            return []
        primary = [a for a in accounts if a.priority != "reserve"]
        reserve = [a for a in accounts if a.priority == "reserve"]
        primary.sort(key=lambda a: (a.order, a.created))
        reserve.sort(key=lambda a: (a.order, a.created))

        mode = config_store.settings.balancing
        if mode == "order":
            # строго по порядку из панели — первый в списке используется первым
            return primary + reserve
        if mode == "first-available":
            # Сначала аккаунты, подтверждённые рабочими, затем по порядку
            # панели. Прежний ключ (order, status…) сортировал по order первым,
            # и режим ничем не отличался от «order».
            key = lambda a: (a.status != ST_OK, a.order, a.created)  # noqa: E731
            return sorted(primary, key=key) + sorted(reserve, key=key)
        if mode == "least-used":
            key = lambda a: (a.last_used, a.order)  # noqa: E731
            return sorted(primary, key=key) + sorted(reserve, key=key)

        # round-robin: вращаем основной и резервный пулы независимо, сохраняя
        # заданный пользователем порядок внутри каждого пула.
        idx = self._next_idx(f"{provider}:{prefix.lower()}" if provider == "custom" and prefix
                             else provider)

        def rotate(group: List[Account]) -> List[Account]:
            if not group:
                return []
            offset = idx % len(group)
            return group[offset:] + group[:offset]

        return rotate(primary) + rotate(reserve)

    # ---------- результат запроса ----------

    def report_success(self, account: Account, *, ms: int = 0) -> None:
        with state._lock:
            was_ok = account.status == ST_OK
            account.mark(ST_OK, "ответил успешно")
            account.last_used = time.time()
            account.record_result(True, ms=ms)
            _inbox_account("proxy_account_ok", account)   # Round 35 (inbox): closes «квота/вход» in «Входящие»
            try:  # Round 35 (quota): requests per hour for the planner's «~N requests/hour» pace
                from . import quota_planner
                quota_planner.note_request(account.id)
            except Exception:
                pass
            if was_ok:
                state.save_accounts_soon()  # изменилась только статистика
            else:
                state.save_accounts(backup=False)

    def report_failure(self, account: Account, exc: Exception, *,
                       model: str = "", ms: int = 0) -> None:
        with state._lock:
            account.last_used = time.time()
            # The account card shows the upstream's message, not its raw JSON body; a provider
            # that words its errors in Russian itself (local models) keeps that wording here.
            public_error = readable_provider_error(
                safe_provider_error(getattr(exc, "message_ru", "") or exc, account))
            _inbox_account("proxy_account_failed", account, exc, public_error)   # Round 35 (inbox, non-blocking)
            if isinstance(exc, ProviderModelUnavailableError):
                # Тариф аккаунта не включает модель: помним это, но не наказываем.
                self.block_model(account, getattr(exc, "model", "") or model, reason=public_error)
                account.record_result(False, ms=ms, error=public_error)
                state.save_accounts_soon()
                return
            if isinstance(exc, ProviderContentFilterError):
                account.record_result(False, ms=ms, error=public_error)
                state.save_accounts_soon()
                return
            if isinstance(exc, ProviderAuthError):
                # Вход отвергнут: пока пользователь не обновит его (или фоновая
                # проверка не подтвердит обратное), запросы не тратятся впустую.
                account.mark(ST_INVALID, public_error)
                account.record_result(False, ms=ms, error=public_error)
                state.save_accounts(backup=False)
                return
            if isinstance(exc, ProviderCaptchaError):
                account.mark(ST_UNKNOWN, public_error)
            else:
                # Лимит, сбой сети, ошибка сервиса: аккаунт НЕ уходит на паузу.
                # Причина видна в карточке и журнале, а сам аккаунт остаётся в
                # ротации — следующий запрос снова попробует его первым по
                # порядку; внутри одного запроса пул переходит к следующему.
                account.status_message = public_error
            account.record_result(False, ms=ms, error=public_error)
            state.save_accounts_soon()


def _inbox_account(name, *args) -> None:
    """Round 35: «Входящие» — rejected sign-in / exhausted quota (app/inbox_hooks.py; never raises, never blocks)."""
    try:
        from . import inbox_hooks
        getattr(inbox_hooks, name)(*args)
    except Exception:  # noqa: BLE001
        pass


pool = AccountPool()
