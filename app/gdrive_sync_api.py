"""Round 29 «Синхронизация»: HTTP routes of MemuAI (``/api/pg/sync/*``) — Settings → Sync.

``register(app)`` is called once from ``playground_api.create_app``.  It also wraps
the app lifespan so the backup scheduler runs while MemuAI is open (MemuAI Proxy
runs its own copy, see :func:`app.gdrive_sync.start_proxy_scheduler`).

Paths never come from the request body: folders and files are picked with the
native dialog of the MemuAI window (or chosen from the auto-detected list), the
same rule the Work and voice-model pickers follow.  Responses never contain
tokens, the client secret or the passphrase — only ``has_*`` flags.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict

from fastapi import Request
from fastapi.responses import JSONResponse

from . import gdrive_sync as core

log = logging.getLogger("memuai.sync.api")

_PICKED: Dict[str, Dict[str, Any]] = {}

def _lang() -> str:
    try:
        from .pgdata import load_preferences
        return "en" if load_preferences().get("language") == "en" else "ru"
    except Exception:
        return "ru"

def _error(exc: BaseException, status: int = 400) -> JSONResponse:
    info = core._err(exc)
    lang = _lang()
    return JSONResponse({"error": info["en"] if lang == "en" else info["ru"], "error_ru": info["ru"],
                         "error_en": info["en"], "code": info["code"]}, status)

async def _body(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}

def _notify(error: Dict[str, str]) -> None:
    lang = _lang()
    try:
        from .desktop_notifications import show_completion
        show_completion("MemuAI — " + ("backup failed" if lang == "en" else "копия не сохранена"),
                        error.get(lang) or error.get("ru") or "")
    except Exception:
        pass

def _paused() -> str:
    try:
        from . import safe_mode
        return "safe_mode" if safe_mode.is_safe_mode() else ""
    except Exception:
        return ""

def register(app, *, service: core.SyncService | None = None) -> core.SyncService:
    svc = service or core.service("memuai")
    svc.notify = _notify
    svc.paused_reason = _paused
    app.state.sync_service = svc

    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(asgi_app):
        if not getattr(app.state, "sync_no_scheduler", False):
            svc.start_scheduler()
        try:
            async with original(asgi_app) as state:
                yield state
        finally:
            svc.stop_scheduler()

    app.router.lifespan_context = lifespan

    def window():
        return getattr(app.state, "window", None)

    @app.get("/api/pg/sync/status")
    async def sync_status():
        try:
            return await asyncio.to_thread(svc.status)
        except Exception as exc:
            return _error(exc)

    @app.post("/api/pg/sync/settings")
    async def sync_settings(request: Request):
        body = await _body(request)
        try:
            return {"settings": await asyncio.to_thread(svc.save_settings, body)}
        except Exception as exc:
            return _error(exc)

    @app.get("/api/pg/sync/stats")
    async def sync_stats():
        try:
            return await asyncio.to_thread(svc.category_stats)
        except Exception as exc:
            return _error(exc)

    @app.post("/api/pg/sync/backup")
    async def sync_backup(request: Request):
        body = await _body(request)
        if svc.busy():
            return _error(core.SyncError("Резервное копирование уже идёт", "A backup is already running", code="busy"), 409)
        if not svc.ready():
            return _error(core.SyncError("Сначала выберите, куда сохранять копии", "Choose where to store backups first",
                                         code="not_ready"))
        label = str(body.get("label") or "")[:120]

        def run() -> None:
            try:
                svc.backup("manual", label=label, force=True)
            except Exception as exc:
                log.info("sync: manual backup failed: %s", core._err(exc)["en"])
        threading.Thread(target=run, name="memuai-sync-manual", daemon=True).start()
        await asyncio.sleep(0.05)
        return {"started": True}

    @app.post("/api/pg/sync/cancel")
    async def sync_cancel():
        svc.cancel_event.set()
        return {"ok": True}

    @app.get("/api/pg/sync/snapshots")
    async def sync_snapshots():
        try:
            return await asyncio.to_thread(svc.list_snapshots)
        except Exception as exc:
            return _error(exc)

    @app.post("/api/pg/sync/preview")
    async def sync_preview(request: Request):
        body = await _body(request)
        try:
            return await asyncio.to_thread(svc.preview_restore, str(body.get("id") or ""), str(body.get("passphrase") or ""))
        except Exception as exc:
            return _error(exc)

    def _cats(body: Dict[str, Any]):
        cats = body.get("categories")
        if not isinstance(cats, list) or not cats:
            return None
        return [str(c) for c in cats if str(c) in core.CATEGORY_IDS]

    @app.post("/api/pg/sync/restore")
    async def sync_restore(request: Request):
        body = await _body(request)
        try:
            return await asyncio.to_thread(svc.restore, str(body.get("id") or ""), _cats(body),
                                           passphrase=str(body.get("passphrase") or ""), mirror=body.get("mirror") is True)
        except Exception as exc:
            return _error(exc)

    @app.post("/api/pg/sync/pin")
    async def sync_pin(request: Request):
        body = await _body(request)
        try:
            pins = await asyncio.to_thread(svc.pin, str(body.get("id") or ""), body.get("pinned") is not False)
            return {"pinned": pins}
        except Exception as exc:
            return _error(exc)

    @app.post("/api/pg/sync/delete")
    async def sync_delete(request: Request):
        body = await _body(request)
        try:
            return await asyncio.to_thread(svc.delete_snapshot, str(body.get("id") or ""))
        except Exception as exc:
            return _error(exc)

    @app.post("/api/pg/sync/export")
    async def sync_export(request: Request):
        body = await _body(request)
        snap = str(body.get("id") or "")
        name = f"MemuAI-backup-{snap[:15] or 'snapshot'}.zip"
        target = ""
        win = window()
        if win is not None:
            try:
                import webview as _wv
                picked = await asyncio.to_thread(win.create_file_dialog, _wv.SAVE_DIALOG, save_filename=name)
                target = str((picked or [""])[0] if isinstance(picked, (list, tuple)) else (picked or "")).strip()
                if not target:
                    return {"ok": False, "cancelled": True}
            except ImportError:
                win = None
        if not target:
            downloads = Path(os.path.expanduser("~")) / "Downloads"
            target = str((downloads if downloads.is_dir() else svc.dir) / name)
        try:
            return await asyncio.to_thread(svc.export_snapshot, snap, target, str(body.get("passphrase") or ""))
        except Exception as exc:
            return _error(exc)

    @app.post("/api/pg/sync/import-file")
    async def sync_import_file():
        win = window()
        if win is None:
            return _error(core.SyncError("Выбор файла доступен только в окне MemuAI", "Picking a file works only in the MemuAI window"))
        try:
            import webview as _wv
            picked = await asyncio.to_thread(win.create_file_dialog, _wv.OPEN_DIALOG, allow_multiple=False,
                                             file_types=("MemuAI backup (*.zip)", "All files (*.*)"))
        except Exception as exc:
            return _error(exc)
        path = str((picked or [""])[0] if isinstance(picked, (list, tuple)) else (picked or "")).strip()
        if not path:
            return {"ok": False, "cancelled": True}
        try:
            info = await asyncio.to_thread(svc.inspect_export, path)
        except Exception as exc:
            return _error(exc)
        token = secrets.token_urlsafe(12)
        for stale in [k for k, v in _PICKED.items() if time.time() - v["ts"] > 3600]:
            _PICKED.pop(stale, None)
        _PICKED[token] = {"path": path, "ts": time.time()}
        return {"ok": True, "token": token, "file": Path(path).name, **info}

    @app.post("/api/pg/sync/restore-file")
    async def sync_restore_file(request: Request):
        body = await _body(request)
        picked = _PICKED.get(str(body.get("token") or ""))
        if not picked:
            return _error(core.SyncError("Выберите файл копии ещё раз", "Pick the backup file again", code="expired"))
        try:
            return await asyncio.to_thread(svc.restore_export, picked["path"], _cats(body), mirror=body.get("mirror") is True)
        except Exception as exc:
            return _error(exc)

    @app.get("/api/pg/sync/safety")
    async def sync_safety():
        return {"items": await asyncio.to_thread(svc.list_safety)}

    @app.post("/api/pg/sync/undo")
    async def sync_undo(request: Request):
        body = await _body(request)
        try:
            return await asyncio.to_thread(svc.undo_restore, str(body.get("name") or ""))
        except Exception as exc:
            return _error(exc)

    @app.post("/api/pg/sync/open-safety")
    async def sync_open_safety():
        folder = svc.safety_dir()
        try:
            if os.name == "nt":  # pragma: no cover
                os.startfile(str(folder))  # type: ignore[attr-defined]
            return {"ok": True}
        except OSError as exc:
            return _error(exc)

    @app.get("/api/pg/sync/folders")
    async def sync_folders():
        return {"folders": await asyncio.to_thread(core.detect_folders)}

    @app.post("/api/pg/sync/pick-folder")
    async def sync_pick_folder(request: Request):
        body = await _body(request)
        detected = str(body.get("detected") or "")
        path = ""
        if detected:

            known = {row["path"] for row in await asyncio.to_thread(core.detect_folders)}
            if detected not in known:
                return _error(core.SyncError("Папка не найдена", "Folder not found"))
            path = detected
        else:
            win = window()
            if win is None:
                return _error(core.SyncError("Выбор папки доступен только в окне MemuAI", "Picking a folder works only in the MemuAI window"))
            try:
                import webview as _wv
                picked = await asyncio.to_thread(win.create_file_dialog, _wv.FOLDER_DIALOG)
            except Exception as exc:
                return _error(exc)
            path = str((picked or [""])[0] if isinstance(picked, (list, tuple)) else (picked or "")).strip()
            if not path:
                return {"ok": False, "cancelled": True}
        try:
            settings = await asyncio.to_thread(svc.save_settings, {"folder_path": path, "target": "folder"})
            return {"ok": True, "settings": settings}
        except Exception as exc:
            return _error(exc)

    @app.post("/api/pg/sync/test")
    async def sync_test():
        def probe() -> Dict[str, Any]:
            target = svc.target()
            started = time.time()
            payload = ('{"probe": %d}' % int(started)).encode()
            target.put_bytes("memuai-probe.json", payload)
            ok = target.get_bytes("memuai-probe.json") == payload
            target.delete("memuai-probe.json")
            if not ok:
                raise core.SyncError("Хранилище вернуло другие данные", "The storage returned different data")
            return {"ok": True, "ms": int((time.time() - started) * 1000), "target": target.describe()}
        try:
            return await asyncio.to_thread(probe)
        except Exception as exc:
            return _error(exc)

    @app.post("/api/pg/sync/auth/start")
    async def sync_auth_start():
        try:
            result = await asyncio.to_thread(svc.auth.start_flow)
            return {"ok": True, **result}
        except Exception as exc:
            return _error(exc)

    @app.post("/api/pg/sync/auth/cancel")
    async def sync_auth_cancel():
        svc.auth.cancel_flow()
        return {"ok": True}

    @app.post("/api/pg/sync/auth/disconnect")
    async def sync_disconnect():
        await asyncio.to_thread(svc.auth.disconnect)
        svc._update_state(account=None)
        return {"ok": True}

    @app.post("/api/pg/sync/auth/account")
    async def sync_account():
        try:
            info = await asyncio.to_thread(svc.auth.account_info)
            if info:
                svc._on_connected(info)
            return {"account": info}
        except Exception as exc:
            return _error(exc)

    return svc
