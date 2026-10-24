"""Round 44: shared, bounded catalogue discovery for manual and automatic refreshes.

An upstream error must never empty a working catalogue. Removal is allowed only
after every usable account in a provider/connection returned a non-empty list.
Manual models and local model ownership remain under their existing managers.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any, Iterable

from fastapi import Request

from .config import config_store
from .providers import get as get_provider
from .providers.base import safe_provider_error
from .state import ModelEntry, _is_available_model, state

log = logging.getLogger("memuai.model_refresh")


def _prefix(account: Any) -> str:
    if account.provider == "custom":
        return str(account.fields.get("name", "")).strip().lower().replace(" ", "-")
    return account.provider


def _sync_local_catalog() -> None:
    from . import local_models
    local_models.sync_catalog()


class ModelRefresher:
    def __init__(self, *, timeout: float = 90, concurrency: int = 3) -> None:
        self.timeout = timeout
        self.concurrency = max(1, concurrency)
        self._lock = asyncio.Lock()
        self._last: dict[str, Any] = {}
        self._started = 0.0
        self._finished_mono = 0.0
        self._provider_finished: dict[str, float] = {}

    def status(self) -> dict[str, Any]:
        ag_accounts = [account for account in state.all_accounts()
                       if account.provider == "antigravity" and account.usable()]
        snapshots = [(getattr(account, "extra", {}) or {}).get("antigravity_model_catalog") or {}
                     for account in ag_accounts]
        checked = [row.get("checked") for row in snapshots
                   if isinstance(row.get("checked"), (int, float)) and row["checked"] > 0]
        catalogues = [{"provider": "antigravity", "checked": min(checked) if checked else None,
                       "accounts": len(ag_accounts), "verified_accounts": len(checked),
                       "source": "api:antigravity", "client": "Antigravity IDE API",
                       "last_error": "; ".join(error for error in self._last.get("errors", [])
                                              if str(error).startswith("antigravity /")),
                       "documentation": "https://www.antigravity.google/docs/models/"}] if ag_accounts else []
        return {"running": self._lock.locked(), "started": self._started,
                "last": dict(self._last),
                "catalogues": catalogues,
                "on_start": bool(getattr(config_store.settings, "model_refresh_on_start", True)),
                "interval_minutes": int(getattr(config_store.settings, "model_refresh_interval", 360))}

    async def refresh(self, *, providers: Iterable[str] = (), accounts: Iterable[str] = (),
                      prune: bool = True, reason: str = "manual") -> dict[str, Any]:
        if self._lock.locked():
            return {"ok": False, "busy": True, "error": "Model catalogue refresh is already running"}
        async with self._lock:
            self._started = time.time()
            wanted = {str(value).strip().lower() for value in providers if str(value).strip()}
            only = {str(value) for value in accounts if str(value)}
            local_error = ""
            if not only and (not wanted or "local" in wanted):
                try:
                    await asyncio.to_thread(_sync_local_catalog)
                except Exception as exc:
                    local_error = "local: " + safe_provider_error(exc)[:400]
            eligible = [(account, account.provider, _prefix(account)) for account in state.all_accounts()
                        if account.usable() and account.provider != "local"
                        and get_provider(account.provider) is not None and _prefix(account)]
            selected = [row for row in eligible
                        if (not wanted or row[1] in wanted) and (not only or row[0].id in only)]
            semaphore = asyncio.Semaphore(self.concurrency)

            async def discover(row: tuple) -> tuple:
                account, provider_id, prefix = row
                async with semaphore:
                    try:
                        provider = get_provider(provider_id)
                        if provider is None:
                            return account, provider_id, prefix, None, ""
                        ids = await asyncio.wait_for(provider.dynamic_models(account), self.timeout)
                        if not isinstance(ids, (list, tuple, set)):
                            return account, provider_id, prefix, None, ""
                        # An empty/partial parse is not evidence that all models were retired.
                        found = list(dict.fromkeys(value.strip() for value in ids
                                                   if isinstance(value, str) and value.strip()
                                                   and value.strip() != "auto" and len(value) <= 512
                                                   and not any(ord(c) < 32 for c in value)))
                        authoritative_empty = bool(getattr(provider, "authoritative_empty_catalog", False))
                        return account, provider_id, prefix, found if found or authoritative_empty else None, ""
                    except asyncio.TimeoutError:
                        return account, provider_id, prefix, None, "model discovery timed out"
                    except Exception as exc:
                        return account, provider_id, prefix, None, safe_provider_error(exc, account)[:400]

            try:
                rows = await asyncio.gather(*(discover(row) for row in selected))
                result = self._apply(rows, eligible, prune=prune, wanted=wanted)
                for _, provider_id, _, _, _ in rows:
                    self._provider_finished[provider_id] = time.monotonic()
                if local_error:
                    result["errors"].append(local_error)
                result.update(ok=True, reason=reason, started=self._started, finished=time.time())
                self._last = result
                return dict(result)
            finally:
                self._finished_mono = time.monotonic()

    @staticmethod
    def _apply(rows: list, eligible: list, *, prune: bool, wanted: set[str]) -> dict[str, Any]:
        added = removed = hidden = availability_updated = 0
        errors: list[str] = []
        touched: set[str] = set()
        returned: dict[tuple[str, str], set[str]] = defaultdict(set)
        successful: dict[tuple[str, str], set[str]] = defaultdict(set)
        required: dict[tuple[str, str], set[str]] = defaultdict(set)
        for account, provider_id, prefix in eligible:
            required[(provider_id, prefix)].add(account.id)
        words = [str(word).lower() for word in config_store.settings.model_hidden_keywords if word]
        with state._lock:
            for account, provider_id, prefix, ids, error in rows:
                # A removed/disabled/reconfigured account cannot repopulate its old connection.
                current = state.accounts.get(account.id)
                if (current is not account or not current.usable() or _prefix(current) != prefix
                        or current.provider != provider_id):
                    continue
                if error:
                    errors.append(f"{account.provider} / {account.label or account.id[:6]}: {error}")
                if ids is None:
                    continue
                key = (provider_id, prefix)
                successful[key].add(account.id)
                returned[key].update(ids)
                provider = get_provider(account.provider)
                for model in ids:
                    model_id = f"{prefix}-{model}"
                    if model_id in state.models:
                        continue
                    try:
                        note = provider.model_note(model) if provider is not None else ""
                    except Exception:
                        note = ""
                    entry = ModelEntry(prefix, account.provider, model,
                                       account_id=account.id if account.provider == "custom" else "",
                                       source="discovered", note=note or f"discovered {time.strftime('%d.%m')}")
                    if _is_available_model(entry):
                        state.models[model_id] = entry
                        added += 1
                        touched.add(prefix)
            if prune:
                for entry in list(state.models.values()):
                    key = (entry.provider, entry.prefix)
                    if (entry.provider == "local" or entry.source == "manual" or key not in returned
                            or successful[key] != required[key] or entry.model in returned[key]):
                        continue
                    del state.models[entry.id]
                    touched.add(entry.prefix)
                    removed += 1
            # A provider may advertise a model which the account's plan/client
            # cannot use. Keep it visible in the admin catalogue, independently
            # of the user's enabled switch, and exclude it from client pickers.
            inspected = {provider_id for _, provider_id, _, ids, _ in rows
                         if ids is not None or bool(getattr(get_provider(provider_id), "authoritative_empty_catalog", False))}
            for entry in state.models.values():
                if entry.provider not in inspected:
                    continue
                provider = get_provider(entry.provider)
                describe = getattr(provider, "model_availability", None)
                if not callable(describe):
                    continue
                accounts = [account for account, provider_id, prefix in eligible
                            if provider_id == entry.provider and prefix == entry.prefix]
                current = describe(entry.model, accounts)
                if isinstance(current, dict) and current != entry.availability:
                    entry.availability = current
                    availability_updated += 1
            for entry in state.models.values():
                if entry.enabled and any(word in f"{entry.prefix}/{entry.model}".lower() for word in words):
                    entry.enabled = False
                    hidden += 1
            if added or removed or hidden or availability_updated:
                state.save_models()
        orphaned = state.prune_orphan_models(wanted) if prune else 0
        return {"added": added, "removed": removed, "pruned": removed + orphaned,
                "hidden": hidden, "scanned": len(rows), "updated": sum(row[3] is not None for row in rows),
                "availability_updated": availability_updated,
                "unavailable": sum(row[3] is None for row in rows), "errors": errors,
                "prefixes": sorted(touched)}

    async def run_loop(self) -> None:
        """Start after the UI is ready; changes to the interval apply without restart."""
        boot = time.monotonic()
        startup_pending = True
        await asyncio.sleep(1)
        while True:
            settings = config_store.settings
            interval = max(0, int(getattr(settings, "model_refresh_interval", 360))) * 60
            startup = startup_pending and bool(getattr(settings, "model_refresh_on_start", True))
            startup_pending = False
            due = bool(interval and time.monotonic() >= (self._finished_mono or boot) + interval)
            if (startup or due) and not self._lock.locked():
                try:
                    await self.refresh(reason="startup" if startup else "interval")
                except Exception:
                    log.exception("automatic model catalogue refresh failed")
            elif interval and not self._lock.locked():
                # Entitlements change more often than the global catalogue.
                # Respect disabling periodic discovery (interval=0), otherwise
                # keep Antigravity aligned within at most fifteen minutes.
                due_providers = [provider_id for provider_id in ("antigravity",)
                                 if any(row.provider == provider_id and row.usable() for row in state.all_accounts())
                                 and time.monotonic() >= self._provider_finished.get(provider_id, boot) + min(interval, 15 * 60)]
                if due_providers:
                    try:
                        await self.refresh(providers=due_providers, reason="provider-interval")
                    except Exception:
                        log.exception("Provider catalogue refresh failed")
            await asyncio.sleep(10)


refresher = ModelRefresher()


def register(app: Any) -> None:
    from .admin_api import _auth

    @app.get("/api/admin/models/auto-refresh")
    async def status(request: Request):
        deny = _auth(request)
        return deny if deny is not None else refresher.status()
