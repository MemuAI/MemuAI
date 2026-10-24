# -*- coding: utf-8 -*-
"""HTTP-маршруты панелей MemuAI (``/api/pg/panels…``) и подключение модели/поиска.

Регистрируется из ``playground_api.create_app`` одной строкой; там же запускается
``panels.panels_loop`` (обновление по расписанию, пока открыто окно MemuAI).
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import panels


async def _body(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _error(exc: Exception, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": str(exc) or type(exc).__name__}, status)


def install_context(*, complete: Callable[[str, list], Awaitable[str]],
                    search: Callable[..., Awaitable[dict]],
                    fetch: Callable[..., Awaitable[dict]], proxy_json=None) -> None:
    """Модель и веб для сборки и обновления панелей (из playground_api)."""

    def default_model() -> str:
        try:
            from .pgdata import load_preferences
            return str(load_preferences().get("default_model") or "")
        except Exception:
            return ""

    from .panels_proxy_usage import configure
    configure(proxy_json)
    panels.set_context(panels.PanelContext(complete=complete, search=search, fetch=fetch,
                                           default_model=default_model))


def register_panel_routes(app: FastAPI) -> None:
    @app.middleware("http")
    async def _panels_loop_binding(request: Request, call_next):
        # create/update run in worker threads; background builds must start on this loop.
        if request.url.path.startswith("/api/pg/panels"):
            panels.bind_loop()
        return await call_next(request)

    try:  # Round 24: Telegram updates of panels (telegram_panels.py)
        from . import telegram_panels
        telegram_panels.register_window_routes(app)
    except Exception:  # noqa: BLE001 — panels work without the Telegram part
        pass

    @app.get("/api/pg/panels")
    async def panels_list():
        try:
            return await asyncio.to_thread(panels.snapshot)
        except Exception as exc:  # noqa: BLE001
            return _error(exc, 500)

    @app.post("/api/pg/panels")
    async def panels_create(request: Request):
        """Создать и сразу заполнить (Раунд 22): встроенные источники — ждём до 20 с, ИИ-сборка —
        коротко, дальше карточка показывает этапы, а окно опрашивает её."""
        body = await _body(request)
        template = str(body.get("template") or "")
        quick = template in ("weather", "currency", "crypto", "stock", "rss", "countdown", "system", "usage",
                             "todo", "notes", "clock", "timers", "proxy", "calendar", "github")
        try:
            panel = await panels.create_and_fill(
                wait=float(body.get("wait") or (20 if quick else 2.5)), template=template,
                params=body.get("params") if isinstance(body.get("params"), dict) else {},
                subject=str(body.get("subject") or ""), title=str(body.get("title") or ""),
                spec=body.get("spec"), values=body.get("values"), runbook=body.get("runbook"),
                cadence=body.get("cadence"), rhythm=str(body.get("rhythm") or ""),
                placements=body.get("placements") if isinstance(body.get("placements"), list) else None,
                size=str(body.get("size") or ""), model=str(body.get("model") or ""),
                options=body.get("options") if isinstance(body.get("options"), dict) else None,
                ai_model=str(body.get("ai_model") or ""))
        except panels.PanelError as exc:
            return _error(exc)
        return {"panel": panels.public_panel(panel)}

    @app.post("/api/pg/panels-preview")
    async def panels_preview(request: Request):
        """Предпросмотр редактора: спецификация вида по ещё не сохранённой настройке содержимого."""
        body = await _body(request)
        kind = str(body.get("kind") or "")
        if kind not in panels.KINDS or kind in ("custom", "sports", "web"):
            return _error(ValueError("Нет встроенной спецификации для этого типа"))
        from . import panels_options
        options = panels_options.clean_options(kind, body.get("options"))
        before = panels_options.clean_options(kind, body.get("before"))
        doc = await asyncio.to_thread(panels.load)
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        try:
            title, spec = panels.typed_spec(kind, params, doc["settings"], options)
        except panels.PanelError as exc:
            return _error(exc)
        return {"title": title, "spec": spec, "options": options, "refetch": panels_options.needs_refetch(kind, before, options)}

    @app.get("/api/pg/panels/{pid}")
    async def panels_read(pid: str):
        doc = await asyncio.to_thread(panels.load)
        try:
            return {"panel": panels.public_panel(panels._find(doc, pid))}
        except panels.PanelError as exc:
            return _error(exc, 404)

    @app.patch("/api/pg/panels/{pid}")
    async def panels_edit(pid: str, request: Request):
        body = await _body(request)
        try:
            panel = await asyncio.to_thread(panels.update_panel, pid, body)
        except panels.PanelError as exc:
            return _error(exc)
        return {"panel": panels.public_panel(panel)}

    @app.post("/api/pg/panels/{pid}/data")
    async def panels_data(pid: str, request: Request):
        body = await _body(request)
        try:
            panel = await asyncio.to_thread(panels.update_data, pid, body.get("values"), body.get("sources"))
        except panels.PanelError as exc:
            return _error(exc)
        return {"panel": panels.public_panel(panel)}

    @app.post("/api/pg/panels/{pid}/refresh")
    async def panels_refresh(pid: str, request: Request):
        """«Обновить сейчас»: ждёт до ~25 с, потом отвечает текущим состоянием (обновление идёт дальше)."""
        body = await _body(request)
        try:
            await asyncio.to_thread(panels.load)
            panels.start_refresh(pid, force=True)
            await panels.wait_refresh(pid, float(body.get("wait") or 25))
            doc = await asyncio.to_thread(panels.load)
            return {"panel": panels.public_panel(panels._find(doc, pid))}
        except panels.PanelError as exc:
            return _error(exc, 404)

    @app.delete("/api/pg/panels/{pid}")
    async def panels_remove(pid: str, purge: int = 0):
        try:
            await asyncio.to_thread(panels.dismiss, pid, purge=bool(purge))
        except panels.PanelError as exc:
            return _error(exc, 404)
        return {"ok": True}

    @app.post("/api/pg/panels/{pid}/feed-reset")
    async def panels_feed_reset(pid: str):
        try:
            panel = await asyncio.to_thread(panels.reset_feed, pid)
        except panels.PanelError as exc:
            return _error(exc)
        return {"panel": panels.public_panel(panel)}

    @app.get("/api/pg/panels-system")
    async def panels_system_live(lang: str = "ru", procs: int = 0, sort: str = "cpu"):
        """Живой замер для карточек «Производительность ПК» (окно опрашивает раз в 1–5 с, пока видно).

        Лёгкий: замер кэшируется ~1 с на все карточки сразу; список процессов — только по ``procs=N``."""
        from . import panels_system
        lang = "en" if lang == "en" else "ru"
        try:
            raw = await asyncio.to_thread(panels_system.sample, lang=lang)
            values = panels.system_values(raw, lang)
            if procs:
                values.update(await asyncio.to_thread(panels_system.top_processes, max(1, min(20, int(procs))),
                                                      "mem" if sort == "mem" else "cpu", lang=lang))
        except Exception as exc:  # noqa: BLE001
            return _error(exc, 500)
        return {"values": values}

    @app.get("/api/pg/panels-timers")
    async def panels_timers():
        """Таймеры, напоминания, секундомер и помодоро «Быстрого доступа» для панели «Помодоро и таймеры».

        Управление — тот же маршрут, что у самого «Быстрого доступа»: POST /api/assistant/timers."""
        try:
            from . import assistant_commands
            snap = await asyncio.to_thread(lambda: assistant_commands.engine().timers.snapshot())
            settings = await asyncio.to_thread(lambda: assistant_commands.engine().settings())
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc) or type(exc).__name__, "available": False}, 503)
        return {"timers": snap, "pomodoro": (settings or {}).get("pomodoro") or {}, "available": True}

    # ------------------------------------------------ To-Do и заметки (Раунд 23, panels_local.py)
    def _local_error(exc: Exception) -> JSONResponse:
        # Round 26: To-Do errors in the UI language (they were Russian-only in the English interface).
        from .pgdata import load_preferences
        text = getattr(exc, "en", "") if load_preferences().get("language") == "en" else ""
        return JSONResponse({"error": text or str(exc)}, 404 if "не найден" in str(exc) else 400)

    @app.get("/api/pg/panels-todo")
    async def panels_todo_lists():
        from . import panels_local
        return await asyncio.to_thread(panels_local.snapshot)

    @app.post("/api/pg/panels-todo")
    async def panels_todo_list_create(request: Request):
        from . import panels_local
        body = await _body(request)
        try:
            return {"list": await asyncio.to_thread(panels_local.create_list, str(body.get("name") or ""), str(body.get("color") or "accent"))}
        except panels_local.LocalError as exc:
            return _local_error(exc)

    @app.patch("/api/pg/panels-todo/{lid}")
    async def panels_todo_list_edit(lid: str, request: Request):
        from . import panels_local
        body = await _body(request)
        try:
            return {"list": await asyncio.to_thread(panels_local.update_list, lid, body)}
        except panels_local.LocalError as exc:
            return _local_error(exc)

    @app.delete("/api/pg/panels-todo/{lid}")
    async def panels_todo_list_delete(lid: str):
        from . import panels_local
        try:
            await asyncio.to_thread(panels_local.delete_list, lid)
        except panels_local.LocalError as exc:
            return _local_error(exc)
        return {"ok": True}

    @app.post("/api/pg/panels-todo/{lid}/items")
    async def panels_todo_add(lid: str, request: Request):
        """{title, due?, priority?, tags?, notes?, subtasks?, repeat?, remind?, quick?: разобрать «завтра !1 #дом»}."""
        from . import panels_local
        body = await _body(request)
        lang = "en" if body.get("lang") == "en" else "ru"
        index = body.get("index")
        try:
            item = await asyncio.to_thread(panels_local.add_item, lid, body, quick=bool(body.get("quick")), lang=lang,
                                           index=int(index) if isinstance(index, (int, float)) else None)
        except panels_local.LocalError as exc:
            return _local_error(exc)
        return {"item": item}

    @app.patch("/api/pg/panels-todo/{lid}/items/{iid}")
    async def panels_todo_edit(lid: str, iid: str, request: Request):
        from . import panels_local
        body = await _body(request)
        lang = "en" if body.get("lang") == "en" else "ru"
        try:
            if body.get("move_to") is not None or body.get("index") is not None:
                item = await asyncio.to_thread(panels_local.move_item, lid, iid, to_list=str(body.get("move_to") or ""),
                                               index=int(body.get("index") if body.get("index") is not None else -1))
            else:
                item = await asyncio.to_thread(panels_local.update_item, lid, iid, body, lang=lang)
        except panels_local.LocalError as exc:
            return _local_error(exc)
        return {"item": item}

    @app.delete("/api/pg/panels-todo/{lid}/items/{iid}")
    async def panels_todo_delete(lid: str, iid: str):
        from . import panels_local
        try:
            return {"item": await asyncio.to_thread(panels_local.delete_item, lid, iid)}
        except panels_local.LocalError as exc:
            return _local_error(exc)

    @app.post("/api/pg/panels-todo/{lid}/restore")
    async def panels_todo_restore(lid: str, request: Request):
        from . import panels_local
        body = await _body(request)
        index = body.get("index")
        try:
            return {"item": await asyncio.to_thread(panels_local.restore_item, lid, body.get("item") or {},
                                                    int(index) if isinstance(index, (int, float)) else None)}
        except panels_local.LocalError as exc:
            return _local_error(exc)

    @app.post("/api/pg/panels-todo/{lid}/clear-done")
    async def panels_todo_clear(lid: str):
        from . import panels_local
        try:
            return {"removed": await asyncio.to_thread(panels_local.clear_done, lid)}
        except panels_local.LocalError as exc:
            return _local_error(exc)

    @app.post("/api/pg/panels-todo-parse")
    async def panels_todo_parse(request: Request):
        """Предпросмотр быстрого ввода: что поймётся из «Купить хлеб завтра 18:00 !1 #дом»."""
        from . import panels_local
        body = await _body(request)
        return {"parsed": panels_local.parse_quick(str(body.get("text") or ""))}

    @app.put("/api/pg/panels-note/{pid}")
    async def panels_note_save(pid: str, request: Request):
        from . import panels_local
        body = await _body(request)
        try:
            return {"note": await asyncio.to_thread(panels_local.save_note, pid, str(body.get("text") or ""))}
        except panels_local.LocalError as exc:
            return _local_error(exc)

    @app.get("/api/pg/panels-usage/detect")
    async def panels_usage_detect():
        """Какие локальные источники лимитов есть на компьютере (без токенов)."""
        from . import panels_usage
        from .panels_proxy_usage import discover
        local, accounts = await asyncio.gather(asyncio.to_thread(panels_usage.detect), discover())
        return {"detected": {**local, "proxy_accounts": accounts}}

    @app.post("/api/pg/panels-usage/test")
    async def panels_usage_test(request: Request):
        """Проверить один аккаунт сейчас: ответ — окна лимитов или понятная стена; токены не возвращаются."""
        from . import panels_usage
        body = await _body(request)
        accounts = panels_usage.clean_accounts([body])
        if not accounts:
            return _error(ValueError("Неизвестный провайдер"))
        account = accounts[0]
        if body.get("id"):
            account["id"] = str(body["id"]) if panels_usage.re.fullmatch(r"a_[a-z0-9]{6,24}", str(body["id"])) else account["id"]
        lang = "en" if body.get("lang") == "en" else "ru"
        result = await panels_usage.refresh_accounts([account], [], lang=lang, force=True)
        return {"account": result["accounts"][0]}

    @app.post("/api/pg/panels-usage/secret")
    async def panels_usage_secret(request: Request):
        """Сохранить вставленный токен аккаунта (DPAPI на Windows). Обратно токен не отдаётся."""
        from . import panels_usage
        body = await _body(request)
        extra = {"account_id": str(body.get("account_ref") or "")[:120]} if body.get("account_ref") else None
        try:
            info = await asyncio.to_thread(panels_usage.put_secret, str(body.get("id") or ""), str(body.get("token") or ""), extra)
        except ValueError as exc:
            return _error(exc)
        return {"ok": True, **info}

    @app.get("/api/pg/panels-usage/secret/{aid}")
    async def panels_usage_secret_info(aid: str):
        from . import panels_usage
        return await asyncio.to_thread(panels_usage.secret_info, aid)

    @app.delete("/api/pg/panels-usage/secret/{aid}")
    async def panels_usage_secret_drop(aid: str):
        from . import panels_usage
        await asyncio.to_thread(panels_usage.drop_secret, aid)
        return {"ok": True}

    @app.post("/api/pg/panels/{pid}/revive")
    async def panels_revive(pid: str):
        try:
            panel = await asyncio.to_thread(panels.revive, pid)
        except panels.PanelError as exc:
            return _error(exc, 404)
        return {"panel": panels.public_panel(panel)}

    @app.post("/api/pg/panels-order")
    async def panels_reorder(request: Request):
        """{placement, ids} — порядок в месте; {id, from, to, index} — перенос между местами."""
        body = await _body(request)
        try:
            if body.get("id") and body.get("to"):
                return await asyncio.to_thread(panels.move, str(body["id"]), str(body.get("from") or ""),
                                               str(body["to"]), int(body.get("index", -1)))
            return await asyncio.to_thread(panels.reorder, str(body.get("placement") or ""),
                                           body.get("ids") if isinstance(body.get("ids"), list) else [])
        except (panels.PanelError, ValueError) as exc:
            return _error(exc)

    @app.post("/api/pg/panels-layout")
    async def panels_layout_save(request: Request):
        """Round 33: {panels: [{id, placements?, order?, size?}], layout?: {zones: {...}}} — one atomic save."""
        body = await _body(request)
        try:
            return await asyncio.to_thread(panels.apply_layout, body.get("panels"), body.get("layout"))
        except panels.PanelError as exc:
            return _error(exc)

    @app.get("/api/pg/panels-settings")
    async def panels_settings():
        doc = await asyncio.to_thread(panels.load)
        return {"settings": doc["settings"]}

    @app.post("/api/pg/panels-settings")
    async def panels_settings_save(request: Request):
        body = await _body(request)
        try:
            return {"settings": await asyncio.to_thread(panels.update_settings, body)}
        except panels.PanelError as exc:
            return _error(exc)

    @app.post("/api/pg/panels-locate")
    async def panels_locate():
        """Пользователь согласился: определить местоположение по IP."""
        try:
            return {"settings": await panels.locate_by_ip()}
        except panels.Wall as wall:
            return JSONResponse({"error": wall.message or wall.status, "wall": wall.status}, 502)
        except Exception as exc:  # noqa: BLE001
            return _error(exc, 502)

    @app.get("/api/pg/panels-geocode")
    async def panels_geocode(q: str = "", lang: str = "ru"):
        try:
            return {"results": await panels.geocode(q, lang)}
        except Exception as exc:  # noqa: BLE001
            return _error(exc, 502)
