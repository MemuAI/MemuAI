"""Panel routes of the newer Proxy tools: local models, the detector and the companion.

The routes stay thin: they check the panel session (the same ``_auth`` as the
rest of /api/admin), read a bounded JSON body and call the modules.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .admin_api import _auth, _json_object

router = APIRouter(prefix="/api/admin")

#: English variants of messages raised by the tool modules (the local models' errors carry their own
#: English text: ``LocalModelError.message_en``).
_EN = {
    "Выберите модель для проверки": "Pick a model to check",
    "Выберите модель для теста": "Pick a model for the test",
    "Выберите хотя бы одну модель для публичного доступа": "Pick at least one model for the public endpoint",
    "Для HTTPS нужны оба файла: сертификат и его ключ": "HTTPS needs both files: the certificate and its key",
    "Сначала включите публичный эндпоинт (шаг 1)": "Turn the public endpoint on first (step 1)",
    "Нужно подтверждение: будет скачан cloudflared с github.com/cloudflare/cloudflared":
        "Confirmation required: cloudflared will be downloaded from github.com/cloudflare/cloudflared",
    "Неизвестное действие": "Unknown action",
    "Токен туннеля выглядит неверно": "The tunnel token looks wrong",
    "Ключ не найден": "Key not found",
    "Неверные IP-адреса или подсети: ": "Invalid IP addresses or subnets: ",
    "Не больше 200 ключей": "No more than 200 keys",
    "Загрузка cloudflared уже идёт": "cloudflared is already being downloaded",
    "Для этой системы нет готового cloudflared — установите его вручную":
        "There is no ready-made cloudflared for this system — install it by hand",
    "GitHub ответил ": "GitHub answered ",
    "Неожиданный адрес загрузки: ": "Unexpected download address: ",
    "Файл слишком большой": "The file is too large",
    "Скачанный файл слишком мал — загрузка не удалась": "The downloaded file is too small — the download failed",
    "В архиве нет cloudflared": "The archive has no cloudflared",
    "Задание не найдено": "Prompt not found",
    "Тест не найден": "Test not found",
    "Нет ни одной включённой модели: добавьте аккаунт, и помощник сможет отвечать":
        "No model is enabled: add an account and the companion can answer",
    "Файл не выбран": "No file selected",
    "Не удалось прочитать файл": "Could not read the file",
}


def _error(message: str, status: int = 400, english: str = "") -> JSONResponse:
    english = english or _EN.get(message) or next(
        (en + message[len(ru):] for ru, en in _EN.items() if message.startswith(ru)), message)
    return JSONResponse({"error": message, "error_en": english}, status_code=status)


def _local_error(exc: Exception, status: int = 400) -> JSONResponse:
    """A local models error in both languages (a ValueError/TypeError has only its own text)."""
    return _error(str(exc), status, getattr(exc, "message_en", "") or str(exc))


async def _body(request: Request, max_bytes: int = 262_144):
    deny = _auth(request)
    if deny:
        return None, deny
    body, error = await _json_object(request, max_bytes=max_bytes)
    return body, error


# --------------------------------------------------------------------------
# Local models
# --------------------------------------------------------------------------

@router.get("/local/status")
async def local_status(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import local_models
    return await asyncio.to_thread(local_models.status)


@router.post("/local/runtime/install")
async def local_runtime_install(request: Request):
    body, error = await _body(request)
    if error:
        return error
    from . import local_models
    try:
        return local_models.install_runtime(str(body.get("backend") or "auto"))
    except local_models.LocalModelError as exc:
        return _local_error(exc)


@router.post("/local/runtime/remove")
async def local_runtime_remove(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import local_models
    await asyncio.to_thread(local_models.remove_runtime)
    return {"ok": True}


@router.post("/local/settings")
async def local_settings(request: Request):
    body, error = await _body(request)
    if error:
        return error
    from . import local_models
    try:
        return await asyncio.to_thread(local_models.update_settings, body)
    except (local_models.LocalModelError, ValueError, TypeError) as exc:
        return _local_error(exc)


@router.get("/local/hf/search")
async def local_hf_search(request: Request, q: str = "", sort: str = "downloads"):
    deny = _auth(request)
    if deny:
        return deny
    from . import local_models
    try:
        return {"results": await asyncio.to_thread(local_models.hf_search, q, sort)}
    except local_models.LocalModelError as exc:
        return _local_error(exc, 502)
    except Exception as exc:
        return _error(f"Hugging Face недоступен: {type(exc).__name__}", 502,
                      f"Hugging Face is unreachable: {type(exc).__name__}")


@router.get("/local/hf/files")
async def local_hf_files(request: Request, repo: str = ""):
    deny = _auth(request)
    if deny:
        return deny
    from . import local_models
    try:
        return await asyncio.to_thread(local_models.hf_files, repo)
    except local_models.LocalModelError as exc:
        return _local_error(exc, 502)
    except Exception as exc:
        return _error(f"Hugging Face недоступен: {type(exc).__name__}", 502,
                      f"Hugging Face is unreachable: {type(exc).__name__}")


@router.post("/local/download")
async def local_download(request: Request):
    body, error = await _body(request)
    if error:
        return error
    from . import local_models
    files = body.get("files") if isinstance(body.get("files"), list) else []
    try:
        # Round 33: «Повторить» resends the wizard's settings (preset) of the first attempt.
        preset = body.get("preset") if isinstance(body.get("preset"), dict) else None
        return local_models.start_download(str(body.get("repo") or ""), [str(item) for item in files],
                                           str(body.get("projector") or ""), str(body.get("draft") or ""),
                                           preset=preset)
    except local_models.LocalModelError as exc:
        return _local_error(exc)


@router.post("/local/download/cancel")
async def local_download_cancel(request: Request):
    body, error = await _body(request)
    if error:
        return error
    from . import local_models
    ok = local_models.cancel_download(str(body.get("id") or ""))
    if body.get("forget"):
        local_models.forget_download(str(body.get("id") or ""))
    return {"ok": ok}


@router.post("/local/pick")
async def local_pick(request: Request):
    """Native file dialog of the Proxy window: returns the chosen .gguf path."""
    deny = _auth(request)
    if deny:
        return deny
    window = getattr(request.app.state, "window", None)
    if not window:
        return _error("Выбор файла доступен только в окне MemuAI Proxy",
                      english="File picking works only in the MemuAI Proxy window")
    import webview
    result = await asyncio.to_thread(window.create_file_dialog, webview.OPEN_DIALOG,
                                     file_types=("GGUF (*.gguf)", "All files (*.*)"))
    if not result:
        return {"ok": False}
    return {"ok": True, "path": str(result[0] if isinstance(result, (list, tuple)) else result)}


@router.post("/local/import")
async def local_import(request: Request):
    body, error = await _body(request)
    if error:
        return error
    from pathlib import Path
    from . import local_models
    try:
        row = await asyncio.to_thread(local_models.register_file, Path(str(body.get("path") or "")),
                                      copy=bool(body.get("copy")), name=str(body.get("name") or ""))
        return {"ok": True, "model": row}
    except local_models.LocalModelError as exc:
        return _local_error(exc)
    except OSError as exc:
        return _error(f"Файл не прочитан: {exc}", english=f"Could not read the file: {exc}")


@router.post("/local/models/{slug}")
async def local_model_update(slug: str, request: Request):
    body, error = await _body(request)
    if error:
        return error
    from . import local_models
    try:
        return {"ok": True, "model": await asyncio.to_thread(local_models.update_model, slug, body)}
    except local_models.LocalModelError as exc:
        return _local_error(exc)


@router.post("/local/models/{slug}/delete")
async def local_model_delete(slug: str, request: Request):
    body, error = await _body(request)
    if error:
        return error
    from . import local_models
    try:
        await asyncio.to_thread(local_models.remove_model, slug, bool(body.get("delete_file")))
        return {"ok": True}
    except local_models.LocalModelError as exc:
        return _local_error(exc)


@router.post("/local/models/{slug}/load")
async def local_model_load(slug: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import local_models
    try:
        port = await asyncio.to_thread(local_models.pool.ensure, slug)
        return {"ok": True, "port": port}
    except local_models.LocalModelError as exc:
        return _local_error(exc)


@router.post("/local/models/{slug}/unload")
async def local_model_unload(slug: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import local_models
    await asyncio.to_thread(local_models.pool.stop, slug)
    return {"ok": True}


@router.post("/local/models/{slug}/test")
async def local_model_test(slug: str, request: Request):
    """A short answer from the model through the normal pipeline (catalogue, logs)."""
    body, error = await _body(request)
    if error:
        return error
    from . import model_call
    prompt = str(body.get("prompt") or "Привет! Представься одним предложением.")[:2000]
    try:
        result = await model_call.complete(f"local-{slug}", [{"role": "user", "content": prompt}], {"max_tokens": 300})
        return {"ok": True, **result}
    except Exception as exc:
        return _error(str(getattr(exc, "message", "") or exc), 502, str(getattr(exc, "message_en", "") or ""))


@router.get("/local/models/{slug}/log")
async def local_model_log(slug: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import local_models
    return {"lines": local_models.pool.log(slug)}


@router.post("/local/scan")
async def local_scan(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import local_models
    return {"servers": await asyncio.to_thread(local_models.scan_servers)}


# --------------------------------------------------------------------------
# Detector
# --------------------------------------------------------------------------

@router.get("/detector/families")
async def detector_families(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import detector
    return {"families": [{"id": row["id"], "label": row["label"], "vendor": row["vendor"]} for row in detector.FAMILIES]}


@router.post("/detector/run")
async def detector_run(request: Request):
    body, error = await _body(request)
    if error:
        return error
    from . import detector
    try:
        return await detector.start(str(body.get("model") or ""), str(body.get("depth") or "quick"),
                                    str(body.get("judge") or ""), str(body.get("claimed") or ""),
                                    str(body.get("lang") or "ru"))
    except ValueError as exc:
        return _error(str(exc))


@router.get("/detector/runs/{run_id}")
async def detector_get(run_id: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import detector
    run = detector.get_run(run_id)
    return run if run else _error("Проверка не найдена", 404)


@router.post("/detector/runs/{run_id}/cancel")
async def detector_cancel(run_id: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import detector
    return {"ok": detector.cancel(run_id)}


@router.get("/detector/history")
async def detector_history(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import detector
    rows = await asyncio.to_thread(detector.history)
    return {"runs": [{key: row.get(key) for key in ("id", "model", "depth", "judge", "state", "started", "finished",
                                                     "error", "report")} for row in rows]}


@router.post("/detector/history/{run_id}/delete")
async def detector_history_delete(run_id: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import detector
    await asyncio.to_thread(detector.delete_history, run_id)
    return {"ok": True}


# --------------------------------------------------------------------------
# Public endpoint: a separate listener with its own keys and limits
# --------------------------------------------------------------------------

@router.get("/public")
async def public_get(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import public_api
    return await asyncio.to_thread(public_api.public_view)


@router.post("/public")
async def public_save(request: Request):
    """Settings of the endpoint. Turning it on needs at least one model to offer."""
    body, error = await _body(request)
    if error:
        return error
    from . import public_api
    changes = {name: body[name] for name in public_api.DEFAULTS if name in body and name != "keys"}
    merged = public_api.normalize({**public_api.store.data(), **changes})
    if merged["enabled"]:
        if not merged["models"]:
            return _error("Выберите хотя бы одну модель для публичного доступа")
        if bool(merged["tls_cert"]) != bool(merged["tls_key"]):
            return _error("Для HTTPS нужны оба файла: сертификат и его ключ")
    public_api.store.update(changes)
    await public_api.listener.apply()
    return public_api.public_view()


@router.post("/public/keys")
async def public_key_add(request: Request):
    body, error = await _body(request)
    if error:
        return error
    from . import public_api
    try:
        created = public_api.store.add_key(body)
    except ValueError as exc:
        return _error(str(exc))
    # Round 24: only the hash is stored — the plaintext key is shown this one time.
    return {**public_api.public_view(), "created": {"id": created["id"], "key": created["key"]}}


@router.post("/public/keys/{key_id}")
async def public_key_edit(key_id: str, request: Request):
    body, error = await _body(request)
    if error:
        return error
    from . import public_api
    try:
        changed = None
        if body.get("delete"):
            public_api.store.delete_key(key_id)
        else:
            changed = public_api.store.edit_key(key_id, body)
    except ValueError as exc:
        return _error(str(exc), 404 if str(exc) == "Ключ не найден" else 400)
    view = public_api.public_view()
    if changed and changed.get("key"):
        view["created"] = {"id": changed["id"], "key": changed["key"]}
    return view


@router.post("/public/tunnel")
async def public_tunnel_action(request: Request):
    """Cloudflare Tunnel: {action: start|stop|download|config, confirm?, mode?, hostname?, token?}."""
    body, error = await _body(request)
    if error:
        return error
    from . import public_api, public_tunnel
    action = str(body.get("action") or "")
    tunnel = public_tunnel.tunnel
    try:
        if action == "config":
            await asyncio.to_thread(tunnel.save_config, {name: body[name] for name in ("mode", "hostname", "token", "autostart") if name in body})
        elif action == "download":
            if body.get("confirm") is not True:
                return _error("Нужно подтверждение: будет скачан cloudflared с github.com/cloudflare/cloudflared")
            await asyncio.to_thread(tunnel.download)
        elif action == "start":
            settings = public_api.store.data()
            if not settings["enabled"] or not public_api.listener.status()["running"]:
                return _error("Сначала включите публичный эндпоинт (шаг 1)")
            await asyncio.to_thread(tunnel.start, settings["port"], tls=bool(settings["tls_cert"] and settings["tls_key"]))
        elif action == "stop":
            await asyncio.to_thread(tunnel.stop)
        else:
            return _error("Неизвестное действие")
    except ValueError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f"{type(exc).__name__}: {exc}"[:300], 500)
    return public_api.public_view()


@router.post("/public/check")
async def public_check(request: Request):
    """Is the listener answering — locally and, as far as it can be seen from here, from outside?"""
    deny = _auth(request)
    if deny:
        return deny
    from . import public_api
    from .providers import http
    settings = public_api.store.data()
    scheme = "https" if settings["tls_cert"] and settings["tls_key"] else "http"
    out: Dict[str, Any] = {"local": False, "outside_ip": "", "outside": None}
    session = await http.get_session("")
    try:
        local_host = "127.0.0.1" if settings["host"] in ("0.0.0.0", "::", "localhost") else settings["host"]
        resp = await session.get(f"{scheme}://{local_host}:{settings['port']}/health", timeout=6, verify=False)
        out["local"] = resp.status_code == 200
    except Exception as exc:
        out["local_error"] = type(exc).__name__
    try:
        resp = await session.get("https://api.ipify.org", timeout=8)
        ip = (resp.text if isinstance(resp.text, str) else "").strip()
        if resp.status_code == 200 and len(ip) <= 45:
            out["outside_ip"] = ip
    except Exception:
        pass
    if out["outside_ip"] and out["local"]:
        # Many routers do not let a computer reach its own outside address ("hairpin"), so a
        # failure here proves nothing; a success does prove the port is forwarded.
        try:
            resp = await session.get(f"{scheme}://{out['outside_ip']}:{settings['port']}/health", timeout=6, verify=False)
            out["outside"] = resp.status_code == 200
        except Exception:
            out["outside"] = False
    return out


# --------------------------------------------------------------------------
# Visual test (no AI grading): the model writes a page, a person looks at it
# --------------------------------------------------------------------------

@router.get("/detector/visual/prompts")
async def visual_prompts(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import visual_test
    return {"prompts": await asyncio.to_thread(visual_test.prompt_list)}


@router.get("/detector/visual/prompts/{prompt_id}")
async def visual_prompt_text(prompt_id: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import visual_test
    row = visual_test.find_prompt(prompt_id)
    return {"id": row["id"], "prompt": row["prompt"]} if row else _error("Задание не найдено", 404)


@router.post("/detector/visual/run")
async def visual_run(request: Request):
    body, error = await _body(request)
    if error:
        return error
    from . import visual_test
    try:
        return await visual_test.start(str(body.get("model") or ""), str(body.get("prompt_id") or ""),
                                       str(body.get("custom") or ""))
    except ValueError as exc:
        return _error(str(exc))


@router.get("/detector/visual/runs")
async def visual_runs(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import visual_test
    return {"runs": await asyncio.to_thread(visual_test.runs)}


@router.post("/detector/visual/runs/{run_id}/cancel")
async def visual_cancel(run_id: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import visual_test
    return {"ok": visual_test.cancel(run_id)}


@router.post("/detector/visual/runs/{run_id}/delete")
async def visual_delete(run_id: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import visual_test
    await asyncio.to_thread(visual_test.delete, run_id)
    return {"ok": True}


@router.get("/detector/visual/runs/{run_id}/answer")
async def visual_answer(run_id: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import visual_test
    text = await asyncio.to_thread(visual_test.answer, run_id)
    return {"text": text} if text is not None else _error("Тест не найден", 404)


@router.get("/detector/visual/runs/{run_id}/page")
async def visual_page(run_id: str, key: str = "", download: str = ""):
    """The generated page. No panel session here: the link opens in a new browser tab, so the
    run's own key in the address is what proves the right to see it.

    The page is model output and is not trusted: ``sandbox`` gives it an origin of its own, so
    its scripts run (and may load libraries from a CDN) but cannot touch the panel or its API.
    """
    from . import visual_test
    html = await asyncio.to_thread(visual_test.page, run_id, key)
    if html is None:
        return Response("Not found", status_code=404, media_type="text/plain")
    return _sandboxed_page(html, f"visual-test-{run_id}.html" if download else "")


def _sandboxed_page(html: str, download_name: str = "") -> Response:
    """Model output as a page with an origin of its own (``sandbox`` never grants the panel's origin).

    The panel itself may frame it (the side-by-side comparison of the visual test): ``frame-ancestors
    'self'`` and ``SAMEORIGIN`` instead of the panel-wide ``DENY``; any other site still cannot.
    """
    headers = {"Content-Security-Policy": "sandbox allow-scripts allow-pointer-lock allow-downloads; frame-ancestors 'self'",
               "X-Frame-Options": "SAMEORIGIN",
               "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}
    if download_name:
        headers["Content-Disposition"] = f'attachment; filename="{download_name}"'
    return Response(html, media_type="text/html; charset=utf-8", headers=headers)


@router.post("/detector/provenance")
async def detector_provenance(request: Request):
    """Local, offline metadata scan of an uploaded image for AI-generation
    provenance hints (C2PA/JUMBF, IPTC DigitalSourceType, EXIF/XMP software
    tags, PNG text chunks). No network call is made. See app/provenance.py."""
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=14_000_000)
    if error is not None:
        return error
    import base64
    raw = str(body.get("data") or "")
    filename = str(body.get("filename") or "")
    language = "ru" if str(body.get("lang") or "ru") == "ru" else "en"
    header, _, payload = raw.partition(",")
    if not payload:
        return _error("Файл не выбран")
    if not header.startswith("data:") or not header.endswith(";base64") or len(header) > 120:
        return _error("Не удалось прочитать файл")
    try:
        image_bytes = base64.b64decode(payload, validate=True)
    except Exception:
        return _error("Не удалось прочитать файл")
    if not image_bytes:
        return _error("Файл не выбран")
    from . import provenance
    result = await asyncio.to_thread(provenance.scan, image_bytes, filename, language)
    return result


# --------------------------------------------------------------------------
# Companion
# --------------------------------------------------------------------------

@router.get("/companion")
async def companion_info(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import companion
    from .config import config_store
    return {"model": companion.default_model(), "saved": config_store.settings.companion_model,
            "kb": sorted(companion.kb_files())}


@router.post("/companion/model")
async def companion_model(request: Request):
    body, error = await _body(request)
    if error:
        return error
    from .config import config_store
    config_store.apply_updates({"companion_model": str(body.get("model") or "")[:300]})
    return {"ok": True}


@router.post("/companion/chat")
async def companion_chat(request: Request):
    body, error = await _body(request, max_bytes=1_048_576)
    if error:
        return error
    from . import companion
    history = [row for row in body.get("history") or [] if isinstance(row, dict)][-24:]
    question = str(body.get("message") or "")[:12000]
    context = body.get("error") if isinstance(body.get("error"), dict) else None
    language = "en" if body.get("lang") == "en" else "ru"

    async def events():
        try:
            async for event in companion.answer(history, question, model=str(body.get("model") or ""),
                                                language=language, error=context):
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            yield "data: " + json.dumps({"type": "done"}) + "\n\n"
        except Exception as exc:  # the real reason reaches the chat, never an empty answer
            message = str(getattr(exc, "message", "") or exc)
            hint = str(getattr(exc, "hint", "") or "")
            yield "data: " + json.dumps({"type": "error", "error": message, "hint": hint}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------------
# Claude Code / Codex CLI install
# --------------------------------------------------------------------------

def _cli_error(exc: Exception, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": str(getattr(exc, "message", "") or exc),
                         "error_en": str(getattr(exc, "message_en", "") or exc)}, status_code=status)


def _cli_connection(body: Dict[str, Any]) -> Dict[str, str]:
    from .cli_integrations import IntegrationError

    connection = str(body.get("connection") or "proxy").strip().lower()
    if connection == "proxy":
        return {"connection": "proxy"}
    if connection != "direct":
        raise IntegrationError("Неизвестный тип подключения CLI", "Unknown CLI connection type")
    base_url = str(body.get("base_url") or "").strip().rstrip("/")
    api_key = body.get("api_key", "")
    try:
        parsed = urlsplit(base_url)
        valid_url = (parsed.scheme in ("http", "https") and bool(parsed.hostname)
                     and not parsed.username and not parsed.password and not parsed.query
                     and not parsed.fragment and len(base_url) <= 2048
                     and not any(char.isspace() for char in base_url))
        _port = parsed.port  # an invalid port must not reach a CLI config file
    except ValueError:
        valid_url = False
    if not valid_url:
        raise IntegrationError(
            "Укажите полный HTTP(S)-адрес своего API без ключа в URL",
            "Enter your API's full HTTP(S) URL without credentials in the URL")
    if not isinstance(api_key, str) or len(api_key) > 4096 or any(char in api_key for char in "\r\n"):
        raise IntegrationError("Некорректный API-ключ", "Invalid API key")
    return {"connection": "direct", "base_url": base_url, "api_key": api_key}


@router.get("/cli/status")
async def cli_status(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import cli_integrations as cli
    return {"claude": cli.claude_status(), "codex": cli.codex_status()}


@router.post("/cli/claude/apply")
async def cli_claude_apply(request: Request):
    body, error = await _body(request)
    if error:
        return error
    from . import cli_integrations as cli
    selection = body.get("selection") if isinstance(body.get("selection"), dict) else {}
    try:
        connection = _cli_connection(body)
        return await asyncio.to_thread(cli.apply_claude, selection, **connection)
    except cli.IntegrationError as exc:
        return _cli_error(exc)


@router.post("/cli/claude/reset")
async def cli_claude_reset(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import cli_integrations as cli
    try:
        return await asyncio.to_thread(cli.reset_claude)
    except cli.IntegrationError as exc:
        return _cli_error(exc)


@router.post("/cli/codex/apply")
async def cli_codex_apply(request: Request):
    body, error = await _body(request)
    if error:
        return error
    from . import cli_integrations as cli
    profiles = body.get("profiles") if isinstance(body.get("profiles"), list) else []
    default = body.get("default") if isinstance(body.get("default"), str) else None
    try:
        connection = _cli_connection(body)
        return await asyncio.to_thread(cli.apply_codex, profiles, default=default, **connection)
    except cli.IntegrationError as exc:
        return _cli_error(exc)


@router.post("/cli/codex/reset")
async def cli_codex_reset(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import cli_integrations as cli
    try:
        return await asyncio.to_thread(cli.reset_codex)
    except cli.IntegrationError as exc:
        return _cli_error(exc)
