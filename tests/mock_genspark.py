"""Мок Genspark для интеграционных тестов: имитирует SSE-ответы
/api/agent/ask_proxy и /api/copilot/ask, is_login, models_config.

Поведение управляется кукой:
  goodcookie…  → нормальный ответ
  limited…     → Rate limit exceeded cf1
  badcookie…   → not login (401 / status -5)
  cfblock…     → Cloudflare HTML (403)
  empty…       → проект без текста
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

app = FastAPI()
last_payloads: Dict[str, Any] = {"agent": None, "copilot": None, "delete": [], "uploaded": 0}


def sse(events: list, delay: float = 0.01):
    async def gen():
        for ev in events:
            yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
            await asyncio.sleep(delay)
    return StreamingResponse(gen(), media_type="text/event-stream")


def good_events(model: str, project_id: str | None, with_reasoning: bool = True) -> list:
    evs: list = [{"type": "project_start", "id": project_id or "proj-mock-" + model.replace(",", "_")[:20]}]
    if with_reasoning:
        evs += [
            {"type": "message_field_delta", "field_name": "thinking", "delta": "Думаю… "},
            {"type": "message_field_delta", "field_name": "thinking", "delta": "хорошо.\n"},
        ]
    evs += [
        {"type": "message_field_delta", "field_name": "content", "delta": f"[{model}] Привет, "},
        {"type": "message_field_delta", "field_name": "content", "delta": "мир!"},
        {"type": "message_result", "content": json.dumps({"detailAnswer": f"[{model}] Привет, мир!"})},
    ]
    return evs


def copilot_events(model: str, project_id: str | None) -> list:
    text = f"[{model}] Привет, мир!"
    evs: list = [{"type": "project_start", "id": project_id or "proj-copilot"}]
    evs += [
        {"type": "message_field_delta", "field_name": "session_state.answer", "delta": text},
        {"type": "message_result", "content": json.dumps({"detailAnswer": text})},
    ]
    return evs


def cookie_of(request: Request) -> str:
    return request.headers.get("cookie", "")


@app.post("/api/agent/ask_proxy")
async def ask_proxy(request: Request):
    cookie = cookie_of(request)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    last_payloads["agent"] = payload
    model = payload.get("ai_chat_model", "?")
    project_id = payload.get("project_id")

    if "badcookie" in cookie:
        return JSONResponse(status_code=401, content={"status": -1, "message": "not login"})
    if "cfblock" in cookie:
        return Response(status_code=403, content="<html><title>Just a moment...</title></html>")
    if "limited" in cookie:
        return sse([{"raw": "Rate limit exceeded cf1"}], delay=0.01)
        # ^ мок отдаёт сырую строку-маркер через обычный json — парсер увидит текст маркера
    if "empty" in cookie:
        return sse([{"type": "project_start", "id": "proj-empty"}])
    if "dupcookie" in cookie:
        # регресс дублей: дельты + кумулятивный снимок + копия в чужом поле
        text = "Привет! Чем я могу помочь вам сегодня?"
        return sse([
            {"type": "project_start", "id": "proj-dup"},
            {"type": "message_field_delta", "field_name": "content", "delta": "Привет! "},
            {"type": "message_field_delta", "field_name": "content", "delta": "Чем я могу помочь вам сегодня?"},
            {"type": "message_field_delta", "field_name": "content", "delta": text},
            {"type": "message_field_delta", "field_name": "session_state.answer", "delta": text},
            {"type": "message_result", "content": json.dumps({"detailAnswer": text})},
        ])
    return sse(good_events(model, project_id))


@app.post("/api/copilot/ask")
async def copilot_ask(request: Request):
    cookie = cookie_of(request)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    last_payloads["copilot"] = payload
    model = (payload.get("extra_data", {}) or {}).get("models", ["?"])[0]
    project_id = None
    if "id=" in str(payload.get("current_query_string", "")):
        project_id = str(payload["current_query_string"]).split("id=")[1].split("&")[0]

    if "badcookie" in cookie:
        return sse([{"status": -5, "message": "not login", "data": {}}])
    if "limited" in cookie:
        return sse([{"raw": "Rate limit exceeded cf2"}])
    return sse(copilot_events(model, project_id))


@app.get("/api/is_login")
async def is_login(request: Request):
    cookie = cookie_of(request)
    if "badcookie" in cookie:
        return {"status": -1, "message": "not login", "data": {}}
    return {"status": 0, "data": {"is_login": True}}


@app.get("/api/user")
async def user(request: Request):
    return {"status": 0, "data": {"email": "mock-user@example.com", "plan_type": "plus"}}


@app.get("/api/models_config")
async def models_config():
    return {"status": 0, "data": {
        "image_models": [
            {"name": "nano-banana-2", "label": "Nano Banana 2"},
            {"name": "gpt-image-2", "label": "GPT Image 2"},
        ],
        "video_models": [
            {"name": "sora-2", "label": "Sora 2"},
        ],
        "audio_models": [],
    }}


@app.get("/api/get_upload_personal_image_url")
async def upload_url(request: Request):
    base = str(request.base_url).rstrip("/")
    return {"data": {"upload_image_url": base + "/mock/upload",
                     "private_storage_url": "mock://private/file"}}


@app.put("/mock/upload")
async def upload(request: Request):
    last_payloads["uploaded"] = len(await request.body())
    return Response(status_code=201)


@app.get("/api/project/delete")
async def project_delete(request: Request):
    last_payloads["delete"].append(request.query_params.get("project_id", ""))
    return {"status": 0, "data": {}}


@app.get("/mock/state")
async def state():
    return last_payloads


@app.post("/mock/reset")
async def reset():
    last_payloads.update({"agent": None, "copilot": None, "delete": [], "uploaded": 0})
    return {"ok": True}
