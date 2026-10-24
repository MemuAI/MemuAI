"""Round 38: routes of the module integration (app/module_integration.py).

GET  /api/pg/modint                               enabled modules' panels, commands, widgets (+ widget settings)
GET  /api/pg/modint/panel/{module}/{panel}         the sandbox page of a module panel (framed by the MemuAI window)
POST /api/pg/modint/data    {module, panel, args}   server data of a built-in panel (contributes.panels[].data)
POST /api/pg/modint/command {module, command, args, confirmed?, surface?, lang?}
POST /api/pg/modint/match   {text}                 a request that is exactly a declared phrase → {module, command, args}
GET  /api/pg/modint/settings?module=&kind=&id=      POST same + {values}  — widget / panel settings
GET  /api/pg/modint/skill?lang=&surface=            the module commands block for a system prompt
"""
from __future__ import annotations

from typing import Any, Callable, Dict

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from . import module_integration as mi


_APP: Dict[str, Any] = {}


def nudge_main(client: Any) -> bool:
    """Hand a command's ``client`` part to the MemuAI main window (Quick Access is another window): start the host
    player / open a module there. No desktop shell (browser dev mode) → False, nothing happens."""
    if not isinstance(client, dict) or not (client.get("music_boot") or client.get("open_module") or client.get("modint_reload")):
        return False
    window = getattr(getattr(_APP.get("app"), "state", None), "window", None)
    if window is None:
        return False
    import json
    import threading
    script = "window.MemuModInt ? (MemuModInt.client(%s), true) : false" % json.dumps(client, ensure_ascii=False)

    def run() -> None:
        try:
            window.evaluate_js(script)
        except Exception:  # noqa: BLE001 - the window is closing
            pass
    threading.Thread(target=run, name="MemuAI module command", daemon=True).start()
    return True


def _lang(value: Any) -> str:
    return "en" if str(value or "") == "en" else "ru"


def register(app, *, env: Callable[[], Dict[str, Any]] = dict) -> None:
    _APP["app"] = app

    @app.get("/api/pg/modint")
    async def modint_list(lang: str = "ru"):
        import asyncio
        return await asyncio.to_thread(mi.public, None, _lang(lang))

    @app.get("/api/pg/modint/panel/{module_id}/{panel_id}")
    async def modint_panel(module_id: str, panel_id: str):
        import asyncio
        try:
            page = await asyncio.to_thread(mi.panel_document, module_id, panel_id, env=env())
        except (KeyError, ValueError):
            return JSONResponse({"error": "Панель модуля недоступна / The module panel is unavailable"}, 404)
        response = Response(page, media_type="text/html; charset=utf-8")
        # Same rules as the module window document (playground_api: /api/pg/modules/{id}/document).
        from .modules_host import load_package, default_root
        from .music.api import csp_sources as music_csp
        try:
            permissions = load_package(default_root(), module_id).get("permissions") or []
        except (KeyError, ValueError):
            permissions = []
        music_src = " " + music_csp() if "music" in permissions else ""
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            f"img-src data: blob:{music_src}; media-src data: blob:{music_src}; font-src data:; "
            "connect-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'")
        return response

    @app.post("/api/pg/modint/data")
    async def modint_data(request: Request):
        """Server data of a built-in module panel (Memu.panel.data() → module_integration_data.py)."""
        import asyncio
        try:
            body = await request.json()
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        try:
            return await asyncio.to_thread(mi.panel_data, str(body.get("module") or ""), str(body.get("panel") or ""),
                                           body.get("args") if isinstance(body.get("args"), dict) else {}, lang=_lang(body.get("lang")))
        except KeyError:
            return JSONResponse({"error": "Данные панели недоступны / The panel data is unavailable"}, 404)

    @app.post("/api/pg/modint/command")
    async def modint_command(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "Некорректный JSON / Invalid JSON"}, 400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Ожидается объект / An object is expected"}, 400)
        surface = str(body.get("surface") or "chat")[:20]
        result = await mi.run_command(str(body.get("module") or ""), str(body.get("command") or ""), body.get("args") or {},
                                      lang=_lang(body.get("lang")), confirmed=body.get("confirmed") is True,
                                      surface=surface, base="" if surface == "chat" else str(request.base_url).rstrip("/"))
        if surface != "chat" and nudge_main(result.get("client")):
            result["client_sent"] = True
        return result

    @app.post("/api/pg/modint/open")
    async def modint_open(request: Request):
        """Quick Access asks the main window to open a module (with an optional route / query)."""
        try:
            body = await request.json()
        except ValueError:
            body = {}
        module_id = str((body or {}).get("module") or "")
        if mi.find(module_id) is None:
            return JSONResponse({"error": "Модуль выключен / The module is off"}, 404)
        detail = body.get("detail") if isinstance(body.get("detail"), dict) else {}
        try:
            from .desktop import show_active_window
            show_active_window()
        except Exception:  # noqa: BLE001 - no desktop shell
            pass
        return {"ok": nudge_main({"open_module": module_id, "detail": {**detail, "from": "quick"}})}

    @app.post("/api/pg/modint/match")
    async def modint_match(request: Request):
        import asyncio
        try:
            body = await request.json()
        except ValueError:
            body = {}
        found = await asyncio.to_thread(mi.match_phrase, str((body or {}).get("text") or ""))
        return {"match": found}

    @app.get("/api/pg/modint/settings")
    async def modint_settings(module: str = "", kind: str = "widget", id: str = ""):
        try:
            return {"values": mi.item_settings(module, "panel" if kind == "panel" else "widget", id)}
        except KeyError:
            return JSONResponse({"error": "Не найдено / Not found"}, 404)

    @app.post("/api/pg/modint/settings")
    async def modint_settings_save(request: Request):
        try:
            body = await request.json()
            values = mi.save_item_settings(str(body.get("module") or ""), "panel" if body.get("kind") == "panel" else "widget",
                                           str(body.get("id") or ""), body.get("values") if isinstance(body.get("values"), dict) else {})
        except KeyError:
            return JSONResponse({"error": "Не найдено / Not found"}, 404)
        except (ValueError, AttributeError):
            return JSONResponse({"error": "Некорректный запрос / Bad request"}, 400)
        # Round 39 (r39-integ): a widget switched in a separate module window (native app window) applies in the MemuAI
        # window at once — no restart; Quick Access re-reads the settings every time it opens.
        nudge_main({"modint_reload": True})
        return {"values": values}

    @app.get("/api/pg/modint/skill")
    async def modint_skill(lang: str = "ru", surface: str = "chat"):
        import asyncio
        return {"prompt": await asyncio.to_thread(mi.skill_prompt, None, lang=_lang(lang), surface=surface)}
