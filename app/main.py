"""Фабрика FastAPI-приложения MemuAI Proxy."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .admin_api import router as admin_router
from .anthropic_api import router as anthropic_router
from .applog import app_log
from .config import config_store
from .openai_api import router as openai_router
from .responses_api import router as responses_router
from .passthrough import router as passthrough_router
from .proxy_tools_api import router as tools_router
from .providers import all_providers, init_registry
from .providers import http as hub_http
from .state import ST_OK, state
from .version import APP_VERSION

log = logging.getLogger("memuai.main")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


async def _watchdog() -> None:
    """Self-healing loop.

    Long-running proxies used to stop answering because a pooled HTTP client
    ended up with dead sockets. The watchdog recycles idle HTTP clients and
    drops expired per-model blocks so the server recovers without a manual
    restart.
    """
    from .providers import http as hub_http
    from .pool import pool
    while True:
        await asyncio.sleep(300)
        try:
            recycled = await hub_http.recycle_idle(900)
            if recycled:
                log.info("watchdog: recycled %s idle HTTP client(s)", recycled)
            pool.expire_blocks()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("watchdog: %s", e)


#: Round 34: what a check answered for a key that is accepted but cannot serve right now (quota spent / no balance).
_LIMIT_MARK_RE = re.compile(r"\bHTTP (?:402|429)\b")


def background_ok_message(message: str) -> str:
    """Status line a successful background check leaves on the card.

    It used to be always «фоновая проверка», which wiped «… исчерпан лимит запросов (HTTP 429)» from a manual
    check — the card's «limit reached» badge turned green again half an hour later."""
    return message if _LIMIT_MARK_RE.search(str(message or "")) else "фоновая проверка"


async def _cookie_checker() -> None:
    """Фоновая проверка аккаунтов всех провайдеров."""
    while True:
        interval_min = config_store.settings.cookie_check_interval
        if interval_min <= 0:
            await asyncio.sleep(60)
            continue
        for acc in list(state.accounts.values()):
            if not acc.enabled:
                continue
            provider = all_providers().get(acc.provider)
            if provider is None:
                continue
            try:
                ok, message = await provider.check(acc)
                acc.last_checked = time.time()
                if ok is True:
                    acc.mark(ST_OK, background_ok_message(message))
                elif ok is False:
                    acc.mark("invalid", message)
                else:
                    acc.status_message = message
                state.save_accounts()
            except Exception as e:  # noqa: BLE001
                log.warning("background check %s: %s", acc.id, e)
            await asyncio.sleep(1.0)
        await asyncio.sleep(interval_min * 60)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from .scheduler import scheduler_loop
        from .telegram_bot import telegram_loop
        from .module_monitor import background_loop
        background: list[asyncio.Task] = []
        sync_service = None
        try:
            background.append(asyncio.create_task(_cookie_checker()))
            background.append(asyncio.create_task(scheduler_loop()))
            background.append(asyncio.create_task(telegram_loop()))
            background.append(asyncio.create_task(_watchdog()))
            background.append(asyncio.create_task(background_loop()))
            # Telegram Mini App: its own 127.0.0.1 listener + tunnel (app/tg_webapp.py), off unless enabled.
            from .tg_webapp import run_manager as tgapp_manager
            background.append(asyncio.create_task(tgapp_manager()))
            # Round 29: «Синхронизация» — automatic backups keep running from the tray (app/gdrive_sync.py).
            from . import gdrive_sync
            sync_service = gdrive_sync.start_proxy_scheduler()
            # Round 29: scheduled messages — sends due messages when the MemuAI window is closed.
            from .scheduled_messages_runner import runner_loop as schedsend_loop
            background.append(asyncio.create_task(schedsend_loop()))
            # Round 35 (quota): «Планировщик квот» — periodic quota snapshots + forecast (app/quota_planner.py).
            from .quota_planner import poll_loop as quota_poll_loop
            background.append(asyncio.create_task(quota_poll_loop()))
            try:
                # Models of a provider whose last account was deleted (Genspark,
                # Notion …) used to stay in the catalogue for good.
                pruned = await asyncio.to_thread(state.prune_orphan_models)
                if pruned:
                    log.info("model catalogue: removed %s model(s) of providers without accounts", pruned)
            except Exception:
                log.exception("model catalogue: orphan cleanup failed")
            try:
                from . import model_seeds
                await asyncio.to_thread(model_seeds.upgrade, state)
            except Exception:
                log.exception("model catalogue: upgrade failed")
            try:
                from . import local_models
                # Files deleted by hand while Proxy was off leave the catalogue here.
                await asyncio.to_thread(local_models.sync_catalog)
            except Exception:
                log.exception("local models: catalogue sync failed")
            from .model_refresh import refresher
            background.append(asyncio.create_task(refresher.run_loop()))
            try:
                from . import public_api
                status = await public_api.listener.apply()
                if status.get("error"):
                    log.warning("public endpoint: %s", status["error"])
            except Exception:
                log.exception("public endpoint: start failed")
            log.info("MemuAI Proxy started: http://%s:%s",
                     config_store.effective_host, config_store.effective_port)
            yield
        finally:
            # Background loops stop first: the scheduler or Telegram bot could
            # otherwise start a request (and relaunch llama-server) after the
            # cleanup below already ran, leaving the process orphaned.
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            if sync_service is not None:
                sync_service.stop_scheduler()
            # Счётчики пишутся раз в 20 секунд; при выходе дописываем хвост,
            # чтобы статистика последних запросов не пропала.
            try:
                from .metrics import metrics
                metrics.flush(force=True)
            except Exception:
                pass
            try:
                state.flush_pending()  # отложенная статистика аккаунтов
            except Exception:
                pass
            try:
                from . import public_api, public_tunnel
                await public_api.listener.stop()
                # cloudflared is a separate process: it must stop with the server, not only at
                # interpreter exit (atexit), or it keeps the outside address of a dead server.
                if public_tunnel.tunnel.running():
                    await asyncio.to_thread(public_tunnel.tunnel.stop)
            except Exception:
                pass
            try:
                from . import local_models
                # llama-server processes must not outlive the Proxy.
                await asyncio.to_thread(local_models.pool.stop_all)
            except Exception:
                pass
            for p in all_providers().values():
                try:
                    await p.close()
                except Exception:
                    pass
            await hub_http.close_all()

    app_log.install()
    init_registry()
    hub_http.configure(verify=config_store.settings.verify_ssl)

    app = FastAPI(title="MemuAI Proxy", version=APP_VERSION,
                  docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        return response

    if config_store.settings.cors_enabled:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
            expose_headers=["*"],
        )

    app.include_router(openai_router)
    app.include_router(responses_router)
    app.include_router(anthropic_router)
    app.include_router(admin_router)
    app.include_router(passthrough_router)
    app.include_router(tools_router)
    # Round 22: capabilities and the full card of a model (app/model_capabilities.py).
    from .model_caps_api import router as model_caps_router
    app.include_router(model_caps_router)
    # Round 33: the local model wizard (app/local_wizard.py) — /api/admin/local/wizard/*.
    from .local_wizard_api import register as register_local_wizard
    register_local_wizard(app)
    # Round 33 (r33-thinking): per-model reasoning levels (Proxy → model card) — /api/admin/reasoning/*.
    from .reasoning_levels_api import register as register_reasoning_levels
    register_reasoning_levels(app)
    # Round 35 (quota): quota planner routes — /api/admin/quota/planner*.
    from .quota_planner import register as register_quota_planner
    register_quota_planner(app)
    # Round 35 (composite): composite models (app/composite_models.py) — /api/admin/composite/* + x-memu-route header.
    from .composite_models_api import register as register_composite_models
    register_composite_models(app)
    # Round 40 (r40-proxy): web versions of models «<model>:web» — /api/admin/web-models/* (app/web_models.py).
    from .web_models_api import register as register_web_models
    register_web_models(app)
    # Round 35 (triggers): «Планирование» webhook trigger — POST /hook/plan/{token} (app/schedule_triggers_ext.py).
    from .schedule_triggers_ext import register_proxy as register_plan_hooks
    register_plan_hooks(app)
    # Round 37 (r37-gemini): Gemini Web cookie import, step-by-step check, sign-in window — /api/admin/geminiweb/*.
    from .geminiweb_api import register as register_geminiweb
    register_geminiweb(app)
    from .model_refresh import register as register_model_refresh
    register_model_refresh(app)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    return app
